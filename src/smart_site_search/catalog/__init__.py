"""Read-only catalogue access and bounded candidate selection."""
import json
import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path

from rapidfuzz import fuzz, process
from rapidfuzz.distance import DamerauLevenshtein

from ..contracts.catalog import Card, Offer, Relation, Role, SourceRef
from ..contracts.search import Candidate, CandidateBatch, ChannelRank
from .normalization import NUMERIC_FILTER_KEYS, search_text

_LATIN_KEYS = "qwertyuiop[]asdfghjkl;'zxcvbnm,."
_CYRILLIC_KEYS = "йцукенгшщзхъфывапролджэячсмитьбю"


class CatalogRepository:
    def __init__(self, path):
        self.path = Path(path).resolve()
        with self._connect() as db:
            meta = {key: json.loads(value) for key, value in db.execute(
                "SELECT key,value FROM catalog_meta WHERE key IN ('catalog_version','taxonomy_version','taxonomy')"
            )}
        if len(meta) != 3:
            raise ValueError("Incomplete catalogue metadata")
        self.catalog_version = meta["catalog_version"]
        self.taxonomy_version = meta["taxonomy_version"]
        self.taxonomy = meta["taxonomy"]

    def _connect(self):
        return closing(sqlite3.connect(f"{self.path.as_uri()}?mode=ro&immutable=1", uri=True))

    def _rows(self, sql, params=()):
        with self._connect() as db:
            return db.execute(sql, params).fetchall()

    def _models(self, model, sql, params=()):
        return [model.model_validate_json(row[0]) for row in self._rows(sql, params)]

    def get_card(self, card_id):
        return next(iter(self._models(Card, "SELECT payload FROM cards WHERE card_id=?", (card_id,))), None)

    def get_cards(self, card_ids):
        ids = tuple(dict.fromkeys(card_ids))
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        return self._models(Card, f"SELECT payload FROM cards WHERE card_id IN ({marks})", ids)

    def get_offer(self, offer_id):
        return next(iter(self._offers("WHERE offer_id=?", (offer_id,))), None)

    def get_offers(self, card_id):
        return self._offers("WHERE card_id=? ORDER BY offer_id", (card_id,))

    def offer_url(self, card_id):
        rows = self._rows(
            "SELECT json_extract(payload, '$.url') FROM offers WHERE card_id=? "
            "AND json_extract(payload, '$.url') IS NOT NULL ORDER BY available DESC, offer_id LIMIT 1",
            (card_id,),
        )
        return rows[0][0] if rows else None

    def _offers(self, clause, params):
        rows = self._rows("SELECT payload,attrs_json,raw_params_json FROM offers " + clause, params)
        return [Offer.model_validate({**json.loads(payload), "attrs": json.loads(attrs),
                                      "raw_params": json.loads(raw_params)})
                for payload, attrs, raw_params in rows]

    def vendor_name(self, vendor_id):
        return next((r[0] for r in self._rows("SELECT name FROM vendors WHERE vendor_id=?", (vendor_id,))), vendor_id)

    def relations(self):
        return self._models(Relation, "SELECT payload FROM relations ORDER BY relation_id")

    def _aliases(self, phrases):
        if not phrases:
            return []
        marks = ",".join("?" for _ in phrases)
        return self._rows(f"SELECT norm_text,target_type,target_id,field FROM aliases WHERE norm_text IN ({marks}) ORDER BY length(norm_text) DESC,norm_text,target_type,target_id", phrases)

    def resolve_entities(self, query):
        words = list(re.finditer(r"[\w-]+", query))
        phrases = {}
        for index, start in enumerate(words[:35]):
            for end in words[index:index+10]:
                text = query[start.start():end.end()]
                if key := search_text(text):
                    phrases.setdefault(key, text)
        entire = search_text(query)
        if entire:
            phrases[entire] = query.strip()
        matches = []
        covered = []
        for key, kind, target, _ in self._aliases(tuple(phrases)):
            if any(key != previous and f" {key} " in f" {previous} " for previous in covered):
                continue
            if kind not in {"vendor", "vendor_alias"}:
                covered.append(key)
            matches.append(dict(text=phrases[key], type="vendor" if kind.startswith("vendor") else (
                "sku" if kind == "sku" else "product"), resolved_id=target, exact=key == entire and not kind.startswith("vendor")))
        if not matches and 1 <= len(words) <= 3 and entire:
            for source, target_keys in ((_LATIN_KEYS, _CYRILLIC_KEYS), (_CYRILLIC_KEYS, _LATIN_KEYS)):
                corrected = query.casefold().translate(str.maketrans(source, target_keys))
                ids = {target for _, kind, target, _ in self._aliases((search_text(corrected),))
                       if kind in {"card", "card_alias", "sku"}}
                if len(ids) == 1:
                    return [dict(text=query.strip(), type="product", resolved_id=ids.pop(),
                                 exact=True, corrected_text=corrected)]
        if words and not matches:
            first = search_text(words[0].group())
            if first.isalpha() and len(first) >= 6:
                vendors = [(key, vendor) for key, vendor in self._rows(
                    "SELECT norm_text,target_id FROM aliases WHERE target_type IN ('vendor','vendor_alias')"
                ) if key.isalpha() and DamerauLevenshtein.distance(first, key, score_cutoff=1) <= 1]
                if vendors and len({vendor for _, vendor in vendors}) == 1:
                    key, vendor = vendors[0]
                    corrected = query[:words[0].start()] + key + query[words[0].end():]
                    return [dict(text=words[0].group(), type="vendor", resolved_id=vendor,
                                 exact=len(words) == 1, corrected_text=corrected)]

        vendors = {item["resolved_id"] for item in matches if item["type"] == "vendor"}
        if vendors and not any(item["type"] in {"sku", "product"} for item in matches):
            for word in words:
                token = search_text(word.group())
                if not re.fullmatch(r"(?=[a-z0-9-]*[a-z])(?=[a-z0-9-]*\d)[a-z0-9-]{3,18}", token):
                    continue
                rows = self._rows("""
                    SELECT f.card_id,c.name_norm FROM cards_fts f JOIN cards c USING(card_id)
                    WHERE cards_fts MATCH ? AND c.kind='hardware' LIMIT 20
                """, (f'"{token}"',))
                for card_id, name in rows:
                    if token in name.split() and vendors.intersection(self.get_card(card_id).vendor_ids):
                        matches.append(dict(text=word.group(), type="product", resolved_id=card_id, exact=False))
        return matches[:12]

    def _numeric_card_ids(self, query_state):
        chosen = []
        roles = tuple(query_state.role_ids)
        for item in query_state.filters:
            if item.strength != "require" or item.key not in NUMERIC_FILTER_KEYS or not item.values:
                continue
            if type(item.values[0]) is not int or item.op not in {"eq", "gte", "lte", "between"}:
                continue
            if item.op == "gte":
                chosen.extend(self._attr_cards(item.key, "<", item.values[0], "DESC", roles))
                chosen.extend(self._attr_cards(item.key, ">=", item.values[0], "ASC", roles))
            elif item.op == "lte":
                chosen.extend(self._attr_cards(item.key, ">", item.values[0], "ASC", roles))
                chosen.extend(self._attr_cards(item.key, "<=", item.values[0], "DESC", roles))
            elif item.op == "eq":
                chosen.extend(self._attr_cards(item.key, None, item.values[0], "ABS", roles))
            else:
                low, high = item.values[0], item.values[1]
                chosen.extend(self._attr_cards(item.key, "between", (low, high), "ASC", roles))
                chosen.extend(self._attr_cards(item.key, "outside", (low, high), "ASC", roles))
        return list(dict.fromkeys(chosen))

    def _attr_cards(self, key, operator, target, direction, roles):
        role_sql = ""
        role_params = ()
        if roles:
            marks = ",".join("?" for _ in roles)
            role_sql = f"AND o.card_id IN (SELECT card_id FROM card_roles WHERE verified=1 AND role_id IN ({marks}))"
            role_params = roles
        number = "CAST(v.typed_value AS INTEGER)"
        where = "v.key=? AND v.knowledge='known' AND v.typed_value GLOB '[0-9]*' AND v.typed_value NOT GLOB '*[^0-9]*'"
        if operator == "between":
            low, high = (int(target[0]), int(target[1]))
            predicate = f"{number} BETWEEN ? AND ?"
            order = f"MIN({number}) ASC"
            params = (key, low, high, *role_params)
        elif operator == "outside":
            low, high = (int(target[0]), int(target[1]))
            predicate = f"({number} < ? OR {number} > ?)"
            order = f"MIN(CASE WHEN {number} < {low} THEN {low} - {number} ELSE {number} - {high} END) ASC"
            params = (key, low, high, *role_params)
        elif operator is None:
            predicate = "1=1"
            order = f"MIN(ABS({number} - {int(target)})) ASC"
            params = (key, *role_params)
        else:
            aggregate = "MAX" if direction == "DESC" else "MIN"
            predicate = f"{number} {operator} ?"
            order = f"{aggregate}({number}) {direction}"
            params = (key, int(target), *role_params)
        rows = self._rows(
            f"""SELECT o.card_id FROM offer_attr_values v
                JOIN offers o ON o.offer_id = v.offer_id
                WHERE {where} AND {predicate} {role_sql}
                GROUP BY o.card_id ORDER BY {order} LIMIT 15""",
            params,
        )
        return [row[0] for row in rows]

    def lookup(self, query, limit=8):
        from ..contracts.intent import QueryState

        return self.retrieve(QueryState(goal=query, mode="direct", search_terms=[query]),
                             card_budget=limit).candidates

    def retrieve(self, query_state, cursor=None, card_budget=24):
        cap = 200
        offset = int(cursor or 0)
        if not 0 <= offset < cap:
            raise ValueError("Catalogue cursor is outside the candidate budget")
        budget = min(max(int(card_budget), 1), cap - offset)
        ranked = defaultdict(dict)
        evidence = {}
        confirmed = set()
        fuzzy_scores = {}

        def add(channel, ids, verified=False):
            for rank, card_id in enumerate(dict.fromkeys(ids), 1):
                ranked[card_id].setdefault(channel, rank)
                if verified:
                    confirmed.add(card_id)

        targets = [e.resolved_id for e in query_state.entities
                   if e.type in {"product", "sku"} and e.usage == "target" and e.resolved_id]
        target_vendors = {e.resolved_id for e in query_state.entities
                          if e.type == "vendor" and e.usage == "target" and e.resolved_id}
        add("exact", [target for target in targets if self.get_card(target)], True)
        if target_vendors and not targets and (query_state.mode == "direct" or
                                               not (query_state.role_ids or query_state.class_ids)):
            for vendor in sorted(target_vendors):
                add("exact", [row[0] for row in self._rows("""
                    SELECT cv.card_id FROM card_vendors cv JOIN cards c USING(card_id)
                    WHERE vendor_id=? ORDER BY c.available_count DESC,c.card_id LIMIT ?
                """, (vendor, cap))], True)
        terms = tuple(dict.fromkeys(key for term in query_state.search_terms if (key := search_text(term))))
        for key, kind, card_id, field in self._aliases(terms):
            if kind not in {"card", "card_alias", "sku"}:
                continue
            add("exact" if kind in {"card", "sku"} else "alias", [card_id], True)
            if kind == "card_alias":
                source = "taxonomy_rule"
            else:
                source = "category" if kind == "card" and card_id.startswith("group:") else "feed"
            evidence[card_id] = [SourceRef(
                source=source, catalog_version=self.catalog_version,
                entity_id=card_id.split(":", 1)[-1], field=field, note=key,
                artifact_version=self.taxonomy_version if source == "taxonomy_rule" else None,
            )] if kind != "sku" else []
        numeric_ids = self._numeric_card_ids(query_state)
        if numeric_ids:
            add("role", numeric_ids, True)
        for role in query_state.role_ids:
            add("role", [r[0] for r in self._rows("""
                SELECT cr.card_id FROM card_roles cr JOIN cards c USING(card_id)
                WHERE role_id=? AND verified=1
                ORDER BY c.available_count=0,c.card_id LIMIT ?
            """, (role, cap))], True)
        for class_id in query_state.class_ids:
            add("role", [r[0] for r in self._rows("""
                SELECT card_id FROM cards WHERE class_id=?
                ORDER BY available_count=0,card_id LIMIT ?
            """, (class_id, cap))], True)
        for term in terms:
            tokens = [word for word in term.split() if len(word) >= 3][:10]
            if not tokens:
                continue
            joiner = " OR " if query_state.role_ids or query_state.class_ids else " AND "
            expression = joiner.join(f'"{word}"' for word in tokens)
            add("fts", [r[0] for r in self._rows("""
                SELECT card_id FROM cards_fts WHERE cards_fts MATCH ?
                ORDER BY bm25(cards_fts,0,9,2,6,4,0.25),card_id LIMIT ?
            """, (expression, cap))])
        if not confirmed and terms:
            names = dict(self._rows("SELECT card_id,name_norm FROM cards ORDER BY card_id"))
            fuzzy = process.extract(terms[0], names, scorer=fuzz.WRatio, limit=8, score_cutoff=85)
            add("fuzzy", [card_id for value, score, card_id in fuzzy])
            fuzzy_scores.update((card_id, score) for _, score, card_id in fuzzy)
        excluded = set(query_state.excluded_card_ids)
        for vendor in query_state.excluded_vendor_ids:
            excluded.update(r[0] for r in self._rows("SELECT card_id FROM card_vendors WHERE vendor_id=?", (vendor,)))
        if target_vendors and not targets:
            eligible = {row[0] for vendor in target_vendors for row in self._rows(
                "SELECT card_id FROM card_vendors WHERE vendor_id=?", (vendor,)
            )}
            excluded.update(ranked.keys() - eligible)
        weights = {"exact": 0, "alias": 1, "role": 2, "fuzzy": 3, "fts": 4}

        def key(card_id):
            ranks = ranked[card_id]
            return (card_id not in numeric_ids, card_id not in targets, card_id not in confirmed,
                    min((rank, weights[channel]) for channel, rank in ranks.items()), card_id)

        ids = sorted(ranked.keys() - excluded, key=key)
        selected = ids[offset:offset+budget]
        end = offset + len(selected)
        exhausted = end >= len(ids)
        truncated = end == cap and not exhausted
        role_matches = defaultdict(list)
        if selected and query_state.role_ids:
            marks = ",".join("?" for _ in selected)
            for card_id, role, payload in self._rows(
                f"SELECT card_id,role_id,payload FROM card_roles "
                f"WHERE verified=1 AND card_id IN ({marks}) ORDER BY role_id",
                selected,
            ):
                if role in query_state.role_ids:
                    role_matches[card_id].append(role)
                    evidence.setdefault(card_id, []).extend(Role.model_validate_json(payload).source_refs)
        return CandidateBatch(
            candidates=[Candidate(
                card_id=card_id, channels=list(ranked[card_id]),
                channel_ranks=[ChannelRank(channel=channel, rank=rank)
                               for channel, rank in ranked[card_id].items()],
                evidence=evidence.get(card_id, []),
                fuzzy_score=fuzzy_scores.get(card_id),
                matched_role_ids=role_matches[card_id],
                relevance="confirmed" if card_id in confirmed else "hypothesis",
            ) for card_id in selected],
            next_cursor=None if exhausted or truncated else str(end),
            examined_card_count=len(selected), exhausted=exhausted, truncated=truncated,
            truncation_reason="candidate_limit" if truncated else None,
        )

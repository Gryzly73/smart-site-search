from dataclasses import dataclass, field
from time import monotonic

from ..catalog.normalization import NUMERIC_FILTER_KEYS
from ..contracts.intent import ExpertPlan, QueryState
from ..contracts.search import ComponentResult, RelatedResult, VerifiedCandidate
from .companions import rule_for
from .constraints import filter_applies
from .verify import BUDGET_PRIORITY, budget_fit, is_budget_ceiling, numeric_distance, verify_card


@dataclass
class SearchMatches:
    primary: list[VerifiedCandidate] = field(default_factory=list)
    related: list[RelatedResult] = field(default_factory=list)
    components: list[ComponentResult] = field(default_factory=list)
    component_filters: dict = field(default_factory=dict)
    truncated: bool = False


@dataclass(frozen=True)
class PageCard:
    card_id: str
    component_id: str
    reserve: bool
    available: bool
    meets_number: bool
    rank: tuple


def _rank(item, query):
    factors = item.rank_factors
    first_rank = min((rank.rank for rank in item.candidate.channel_ranks), default=999)
    distance = numeric_distance(query.filters, item.selected_offer.conditions) if item.selected_offer else 0
    status, _ = budget_fit(query.filters, item.selected_offer) if item.selected_offer else (None, None)
    required = {f.filter_id for f in query.filters if f.strength == "require"}
    mismatches = sum(c.status == "mismatch" and c.filter_id in required
                     for c in item.selected_offer.conditions) if status else 0
    return (item.match_level != "full", BUDGET_PRIORITY[status], factors.relevance_tier,
            factors.identity_rank, mismatches,
            first_rank, -factors.preference_matches, factors.availability_rank, distance,
            factors.price_minor if factors.price_minor is not None else float("inf"),
            item.candidate.card_id)


def _meets_number(verified, query) -> bool:
    offer = verified.selected_offer
    if offer is None or offer.available is True:
        return False
    checks = {check.filter_id: check for check in offer.conditions}
    numeric = [item for item in query.filters if item.strength == "require" and item.key in NUMERIC_FILTER_KEYS]
    if not numeric:
        return False
    return all(
        checks.get(item.filter_id) is not None and checks[item.filter_id].status == "match" for item in numeric
    )


def _has_required_number(query) -> bool:
    return any(item.strength == "require" and item.key in NUMERIC_FILTER_KEYS for item in query.filters)


def _ordered_page(groups, limit):
    cards = []
    lookup = {}
    for reserve, items, query in groups:
        for item in items:
            if item.selected_offer is None:
                continue
            lookup[item.candidate.card_id] = item
            cards.append(PageCard(
                card_id=item.candidate.card_id,
                component_id=item.candidate.component_id or item.candidate.card_id,
                reserve=reserve,
                available=item.selected_offer.available is True,
                meets_number=_meets_number(item, query),
                rank=_rank(item, query),
            ))
    return [lookup[card.card_id] for card in compose_expert_page(cards, limit)]


def compose_expert_page(cards, limit):
    """Keep a required numeric component visible before the page fills with other leaders."""
    chosen = []
    seen = set()

    def take(card):
        if card is None or card.card_id in seen or len(chosen) >= limit:
            return
        chosen.append(card)
        seen.add(card.card_id)

    groups = {}
    order = []
    for card in cards:
        if card.component_id not in groups:
            order.append(card.component_id)
            groups[card.component_id] = []
        groups[card.component_id].append(card)
    for component_id in order:
        group = groups[component_id]
        if not group[0].reserve:
            continue
        available = next((card for card in group if card.available), None)
        unavailable = next((card for card in group if card.meets_number and not card.available), None)
        take(available or group[0])
        if unavailable is not None and (available is None or unavailable.card_id != available.card_id):
            take(unavailable)
    for component_id in order:
        group = groups[component_id]
        if group[0].reserve:
            continue
        take(group[0])
    for card in sorted((card for card in cards if card.card_id not in seen), key=lambda card: card.rank):
        take(card)
    return chosen[:limit]


def _retrieve(catalog, query, deadline, emit, *, cap, component_id=None, desired=4):
    cursor = None
    seen = set()
    results = []
    targets = {entity.resolved_id for entity in query.entities
               if entity.usage == "target" and entity.type in {"product", "sku"}
               and entity.resolved_id and ":" in entity.resolved_id}
    limits = sorted({min(24, cap), min(72, cap), cap})
    exhausted = False
    truncated = False
    with_deadline = bool(deadline)
    has_budget = any(is_budget_ceiling(f) for f in query.filters)
    for capacity in limits:
        if (with_deadline and monotonic() >= deadline) or capacity <= len(seen):
            if with_deadline and monotonic() >= deadline:
                truncated = True
                break
            continue
        start = monotonic()
        batch = catalog.retrieve(query, cursor, capacity - len(seen))
        new = [candidate for candidate in batch.candidates if candidate.card_id not in seen]
        emit("retrieval_completed", {
            "stage": capacity, "channels": sorted({c for candidate in new for c in candidate.channels}),
            "candidate_ids": [candidate.card_id for candidate in new], "new_count": len(new),
            "total_count": len(seen) + len(new), "exhausted": batch.exhausted,
            "truncated": batch.truncated, "duration_ms": round((monotonic() - start) * 1000),
        })
        for candidate in new:
            seen.add(candidate.card_id)
            if monotonic() >= deadline:
                truncated = True
                break
            if targets and candidate.card_id not in targets:
                continue
            if (query.role_ids or query.class_ids) and candidate.relevance != "confirmed":
                continue
            card = catalog.get_card(candidate.card_id)
            if card is None:
                continue
            if any(item.key == "kind" and item.strength == "require" and item.op in {"eq", "in"}
                   and card.kind not in item.values for item in query.filters):
                continue
            candidate = candidate.model_copy(update={"component_id": component_id})
            started = monotonic()
            verified = verify_card(candidate, card, catalog.get_offers(card.card_id), query, deadline)
            selected = verified.selected_offer
            emit("offer_verification_completed", {
                "card_id": card.card_id, "component_id": component_id,
                "selected_offer_id": selected.offer_id if selected else None,
                "match_level": verified.match_level,
                "conditions": [condition.model_dump(mode="json")
                               for condition in selected.conditions] if selected else [],
                "checks_complete_for_card": verified.checks_complete_for_card,
                "duration_ms": round((monotonic() - started) * 1000),
            })
            if selected:
                results.append(verified)
            truncated |= not verified.checks_complete_for_card
        exhausted = batch.exhausted
        cursor = batch.next_cursor
        if truncated or exhausted or cursor is None:
            break
        if query.mode == "direct" and (targets or not has_budget) and any(
            item.candidate.relevance == "confirmed" for item in results
        ):
            break
        if (query.mode != "direct" or has_budget) and sum(
            item.candidate.relevance == "confirmed" for item in results
        ) >= desired and (not any(item.strength == "require" for item in query.filters)
                          or any(item.match_level == "full" for item in results)):
            break
    if not exhausted and (len(seen) >= cap or (batch.truncated if seen else False)):
        truncated = True
    return sorted(results, key=lambda item: _rank(item, query)), truncated


def _reserve_over_budget(items, query, limit):
    if (len(items) <= limit or items[0].match_level == "full"
            or not any(is_budget_ceiling(f) for f in query.filters)):
        return items

    def shows_over(item):
        status, _ = budget_fit(query.filters, item.selected_offer)
        return status == "over" or (status == "within" and any(
            budget_fit(query.filters, offer)[0] == "over" for offer in item.alternate_offers
        ))

    if any(shows_over(item) for item in items[:limit]):
        return items
    for index in range(limit, len(items)):
        if shows_over(items[index]):
            return [*items[:limit - 1], items[index], *items[limit - 1:index], *items[index + 1:]]
    return items


def collect_matches(catalog, query: QueryState, plan: ExpertPlan | None, deadline: float,
                    emit, *, limit=8, seen_card_ids=()) -> SearchMatches:
    matches = SearchMatches()
    if query.omit_seen:
        query = query.model_copy(update={
            "excluded_card_ids": list(dict.fromkeys([*query.excluded_card_ids, *seen_card_ids]))
        })
    if query.mode != "expert":
        matches.primary, matches.truncated = _retrieve(catalog, query, deadline, emit, cap=200)
        matches.primary = _reserve_over_budget(matches.primary, query, limit)
    elif plan is None and any(item.key.startswith("vram_") for item in query.filters):
        gpu_query = query.model_copy(update={
            "role_ids": ["gpu"], "class_ids": [], "search_terms": ["видеокарта"], "entities": [],
            "filters": [item for item in query.filters if item.key.startswith("vram_")
                        or item.key in {"available", "vendor_id", "card_id"} or is_budget_ceiling(item)],
        })
        verified, matches.truncated = _retrieve(
            catalog, gpu_query, deadline, emit, cap=50, component_id="gpu-verified", desired=2,
        )
        matches.primary = _ordered_page([(True, verified, gpu_query)], limit)
        full = [item.candidate.card_id for item in verified if item.match_level == "full"]
        partial = [item.candidate.card_id for item in verified if item.match_level == "partial"]
        matches.components.append(ComponentResult(
            component_id="gpu-verified", label="Видеокарта",
            rationale="Проверены явно запрошенные характеристики видеокарты.",
            status="covered" if full else "partial" if partial else "missing",
            full_card_ids=full, partial_card_ids=partial,
            missing_reason="Экспертный план пока недоступен. Совместимость комплекта не проверялась.",
            compatibility_status="not_verified", compatibility_evidence=[],
        ))
    elif plan is not None:
        groups = []
        for component in plan.components:
            selected_filters = [item for item in query.filters if filter_applies(
                item, role_ids=component.role_ids, component_id=component.component_id,
            )]
            selected_filters.extend(component.filters)
            matches.component_filters[component.component_id] = selected_filters
            component_query = query.model_copy(update={
                "role_ids": component.role_ids, "class_ids": [], "search_terms": component.search_terms,
                "filters": selected_filters,
                "entities": [], "unresolved_constraints": query.unresolved_constraints,
            })
            verified, truncated = _retrieve(catalog, component_query, deadline, emit,
                                            cap=50, component_id=component.component_id, desired=2)
            full = [c.candidate.card_id for c in verified if c.match_level == "full"]
            partial = [c.candidate.card_id for c in verified if c.match_level == "partial"]
            groups.append((component.required and _has_required_number(component_query), verified, component_query))
            matches.truncated |= truncated
            matches.components.append(ComponentResult(
                component_id=component.component_id, label=component.label,
                rationale=component.rationale, unknowns=component.unknowns,
                status="covered" if full else "partial" if partial else "missing",
                full_card_ids=full, partial_card_ids=partial,
                missing_reason=None if full else ("В проверенной выборке условия подтверждены частично."
                                                  if partial else "В проверенной выборке подходящих позиций нет."),
                compatibility_status="not_verified", compatibility_evidence=[],
            ))
        verified_count = sum(len(items) for _reserve, items, _query in groups)
        matches.primary = _ordered_page(groups, max(limit, verified_count))
    primary_ids = list(dict.fromkeys(item.candidate.card_id for item in matches.primary))
    emit("ranking_completed", {
        "ordered_card_ids": primary_ids[:limit],
        "rank_factors": {item.candidate.card_id: item.rank_factors.model_dump()
                         for item in matches.primary[:limit]},
        "business_signals_mock": False,
    })
    remaining = limit - len(primary_ids)
    role_anchor_possible = (
        query.mode == "functional" and not query.unresolved_constraints
        and query.role_ids and not any(entity.usage == "target" for entity in query.entities)
    )
    anchor = _shown_anchor(matches.primary, limit)
    anchor_card = catalog.get_card(anchor.candidate.card_id) if anchor is not None else None
    if (rule_for(anchor_card) is None and remaining > 0 and (primary_ids or role_anchor_possible)
            and query.mode != "expert" and monotonic() < deadline):
        matches.related = _related(catalog, query, matches.primary, set(primary_ids), remaining,
                                   deadline, emit)
    return matches


def _shown_anchor(primary, limit):
    shown = []
    seen = set()
    for item in primary:
        card_id = item.candidate.card_id
        if card_id in seen or item.selected_offer is None:
            continue
        seen.add(card_id)
        shown.append(item)
        if len(shown) == limit:
            break
    full = [item for item in shown if item.match_level == "full"]
    if full:
        return full[0]
    return shown[0] if shown else None


def _related(catalog, query, primary, excluded, remaining, deadline, emit):
    results = []
    anchors = [catalog.get_card(item.candidate.card_id) for item in primary[:2]]
    for relation in catalog.relations():
        anchor = next((card for card in anchors if card is not None and (
            card.class_id == relation.source.id if relation.source.type == "class"
            else any(role.role_id == relation.source.id and role.verified for role in card.roles)
        )), None)
        anchor_role = (relation.source.id if not anchors and relation.source.type == "role"
                       and relation.source.id in query.role_ids else None)
        if (anchor is None and anchor_role is None) or monotonic() >= deadline:
            continue
        relation_query = QueryState(
            goal=relation.reason, mode="functional",
            role_ids=[relation.target.id] if relation.target.type == "role" else [],
            class_ids=[relation.target.id] if relation.target.type == "class" else [],
            filters=[item for item in query.filters if item.key in relation.propagated_filter_keys
                     or item.strength == "exclude" and item.key in {"vendor_id", "card_id"}],
            excluded_card_ids=[*query.excluded_card_ids, *sorted(excluded)],
            excluded_vendor_ids=query.excluded_vendor_ids,
        )
        batch = catalog.retrieve(relation_query, None, min(24, remaining * 4))
        for candidate in batch.candidates:
            card = catalog.get_card(candidate.card_id)
            if card is None or candidate.card_id in excluded or monotonic() >= deadline:
                continue
            target_matches = (
                card.class_id == relation.target.id if relation.target.type == "class"
                else any(role.role_id == relation.target.id and role.verified for role in card.roles)
            )
            if not target_matches:
                continue
            verified = verify_card(candidate, card, catalog.get_offers(card.card_id), relation_query, deadline)
            if verified.selected_offer is None or verified.selected_offer.available is not True:
                continue
            result = RelatedResult(
                verified=verified, relation_id=relation.relation_id, anchor_card_id=anchor.card_id if anchor else None,
                anchor_role_id=anchor_role,
                reason=relation.reason, compatibility_status="not_verified", compatibility_evidence=[],
            )
            results.append(result)
            excluded.add(card.card_id)
            emit("related_selected", {
                "card_id": card.card_id, "relation_id": relation.relation_id,
                "anchor_card_id": result.anchor_card_id, "anchor_role_id": result.anchor_role_id,
                "propagated_filter_ids": [item.filter_id for item in relation_query.filters],
            })
            # Prefer coverage of different directed relations before filling the section.
            break
        if len(results) >= remaining:
            break
    return results

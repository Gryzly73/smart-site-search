"""Streaming YML to an immutable local catalog snapshot."""
import gzip
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..contracts.catalog import Attribute, Card, Money, Offer, Relation, Role, SourceRef
from ..taxonomy import load_taxonomy, read_resource, taxonomy_digest
from .normalization import (
    NORMALIZER_VERSION, Param, currency_and_price, normalize_attributes, normalized,
    order_bounds, search_text,
)

SCHEMA_VERSION = 1
SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE catalog_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE categories(category_id TEXT PRIMARY KEY, parent_id TEXT, name TEXT NOT NULL);
CREATE TABLE vendors(vendor_id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE classes(class_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE roles(role_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE cards(
    card_id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, name_norm TEXT NOT NULL,
    class_id TEXT NOT NULL REFERENCES classes(class_id), offer_count INTEGER NOT NULL,
    available_count INTEGER NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE offers(
    offer_id TEXT PRIMARY KEY, card_id TEXT NOT NULL REFERENCES cards(card_id) DEFERRABLE INITIALLY DEFERRED,
    currency TEXT, price_minor INTEGER CHECK(price_minor IS NULL OR price_minor > 0),
    price_state TEXT NOT NULL, available INTEGER CHECK(available IN (0, 1)),
    vendor_code TEXT, raw_currency TEXT, raw_price TEXT, raw_units_json TEXT NOT NULL,
    raw_params_json TEXT NOT NULL, attrs_json TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE offer_attr_values(
    offer_id TEXT NOT NULL REFERENCES offers(offer_id), key TEXT NOT NULL,
    typed_value NOT NULL, unit TEXT, knowledge TEXT NOT NULL, raw_index INTEGER
);
CREATE INDEX offers_card ON offers(card_id, available, price_minor);
CREATE TABLE card_roles(
    card_id TEXT NOT NULL REFERENCES cards(card_id), role_id TEXT NOT NULL REFERENCES roles(role_id),
    verified INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(card_id, role_id)
);
CREATE TABLE card_vendors(
    card_id TEXT NOT NULL REFERENCES cards(card_id), vendor_id TEXT NOT NULL REFERENCES vendors(vendor_id),
    PRIMARY KEY(card_id, vendor_id)
);
CREATE TABLE aliases(
    norm_text TEXT NOT NULL, target_type TEXT NOT NULL, target_id TEXT NOT NULL,
    field TEXT NOT NULL, PRIMARY KEY(norm_text, target_type, target_id)
);
CREATE TABLE relations(relation_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE VIRTUAL TABLE cards_fts USING fts5(
    card_id UNINDEXED, name, vendor, aliases, roles_text, description, tokenize='unicode61 remove_diacritics 2'
);
"""
INDEXES = """
CREATE INDEX cards_class ON cards(class_id, card_id);
CREATE INDEX offer_attrs_offer ON offer_attr_values(offer_id, key);
CREATE INDEX card_roles_role ON card_roles(role_id, card_id);
CREATE INDEX card_vendors_vendor ON card_vendors(vendor_id, card_id);
"""


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _open_feed(path: Path):
    with path.open("rb") as source:
        magic = source.read(2)
    return gzip.open(path, "rb") if magic == b"\x1f\x8b" else path.open("rb")


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _text(element: ET.Element) -> str:
    return element.text.strip() if element.text else ""


def _safe_url(url: str) -> str | None:
    return url if re.fullmatch(r"https?://[^\s]+", url, re.I) else None


def _availability(raw: str | None) -> bool | None:
    return {"true": True, "false": False, "1": True, "0": False}.get(raw or "")


def _ref(source, version: str, entity_id: str, field: str, **kwargs) -> SourceRef:
    return SourceRef(source=source, catalog_version=version, entity_id=entity_id, field=field, **kwargs)


@dataclass
class Facet:
    unit: str | None
    values: dict = field(default_factory=dict)
    low: int | None = None
    high: int | None = None
    range_ref: SourceRef | None = None

    def add(self, attribute: Attribute) -> None:
        ref = next(iter(attribute.source_refs), None)
        for value in attribute.values:
            self.values.setdefault(value, ref)
        if attribute.min_value is not None and attribute.max_value is not None:
            self.low = attribute.min_value if self.low is None else min(self.low, attribute.min_value)
            self.high = attribute.max_value if self.high is None else max(self.high, attribute.max_value)
            self.range_ref = self.range_ref or ref

    def model(self, key: str) -> Attribute:
        sources = dict.fromkeys(
            ref.model_dump_json(exclude_none=True) for ref in [*self.values.values(), self.range_ref] if ref
        )
        return Attribute(
            key=key, unit=self.unit, knowledge="known",
            values=list(self.values), min_value=self.low, max_value=self.high,
            source_refs=[SourceRef.model_validate_json(ref) for ref in sources],
        )


@dataclass
class Accumulator:
    kind: str
    name: str
    name_entity_id: str
    name_source: str
    shelves: set[str] = field(default_factory=set)
    vendors: set[str] = field(default_factory=set)
    description: str | None = None
    image_url: str | None = None
    url: str | None = None
    count: int = 0
    available_count: int = 0
    prices: dict[str, tuple[int, int]] = field(default_factory=dict)
    facets: dict[str, Facet] = field(default_factory=dict)
    vm_backup_offer: str | None = None

    def add(self, offer: Offer, fields: dict[str, str], shelf: str, vendor_id: str) -> None:
        self.count += 1
        self.available_count += offer.available is True
        if shelf:
            self.shelves.add(shelf)
        if vendor_id:
            self.vendors.add(vendor_id)
        self.image_url = self.image_url or offer.image_url
        if self.kind == "hardware":
            self.url = offer.url
        description = fields.get("description", "")
        if self.description is None and len(description) >= 380 and " • " not in description:
            self.description = description[:1800]
        if offer.price:
            currency, value = offer.price.currency, offer.price.amount_minor
            low, high = self.prices.get(currency, (value, value))
            self.prices[currency] = (min(low, value), max(high, value))
        for attr in offer.attrs:
            if attr.knowledge != "known":
                continue
            self.facets.setdefault(attr.key, Facet(attr.unit)).add(attr)
        if self.vm_backup_offer is None and re.search(
            r"\b(?:виртуальн\w+ машин\w+|платформ\w+ виртуализац\w+|virtual machines?)\b",
            normalized(offer.name),
        ):
            self.vm_backup_offer = offer.offer_id


class CatalogBuilder:
    def __init__(self, conn: sqlite3.Connection, version: str, taxonomy_version: str):
        self.conn = conn
        self.version = version
        self.taxonomy_version = taxonomy_version
        self.taxonomy = load_taxonomy()
        self.classification = read_resource("classification")
        self.alias_rules = read_resource("aliases")
        self.card_alias_patterns = [
            (re.compile(pattern, re.I), aliases) for pattern, aliases in self.alias_rules["card"]
        ]
        self.name_patterns = {
            kind: [(class_id, re.compile(pattern), roles) for class_id, pattern, roles in rules]
            for kind, rules in self.classification["rules"].items()
            if kind != "shelves"
        }
        self.shelf_patterns = {
            kind: [(class_id, re.compile(pattern)) for class_id, pattern in rules]
            for kind, rules in self.classification["shelf_classes"].items()
        }
        self.categories: dict[str, tuple[str | None, str]] = {}
        self.cards: dict[str, Accumulator] = {}
        self.vendors: dict[str, str] = {}
        self.stats: Counter = Counter()
        self.feed_date = None

    def read(self, path: Path) -> None:
        with _open_feed(path) as source:
            stack = []
            for event, element in ET.iterparse(source, events=("start", "end")):
                tag = _tag(element)
                if event == "start":
                    if not stack:
                        if tag != "yml_catalog":
                            raise ValueError("Expected yml_catalog root")
                        self.feed_date = element.get("date")
                    stack.append(element)
                    continue
                if tag == "category":
                    self.add_category(element)
                elif tag == "offer":
                    self.add_offer(element)
                if tag in {"offer", "category"}:
                    # Unlink processed children as well as clearing their contents.
                    stack[-2].remove(element)
                    element.clear()
                stack.pop()

    def add_category(self, element: ET.Element) -> None:
        category_id = element.get("id")
        if not category_id or category_id in self.categories:
            raise ValueError(f"Missing or duplicate category ID: {category_id!r}")
        self.categories[category_id] = (element.get("parentId"), _text(element))

    def classify(self, kind: str, name: str, shelves: set[str]) -> tuple[str, list[str], str | None]:
        norm_name = normalized(name)
        for class_id, pattern, extra_roles in self.name_patterns[kind]:
            if pattern.search(norm_name):
                return class_id, extra_roles, None
        for shelf_id in sorted(shelves):
            _, shelf_name = self.categories.get(shelf_id, (None, ""))
            for class_id, pattern in self.shelf_patterns[kind]:
                if pattern.search(normalized(shelf_name)):
                    return class_id, [], shelf_id
        return "unclassified", [], None

    def add_offer(self, element: ET.Element) -> None:
        offer_id = element.get("id")
        if not offer_id:
            raise ValueError("Offer without source ID")
        fields = {}
        params = []
        for child in element:
            tag = _tag(child)
            if tag == "param":
                params.append(Param(child.get("name", ""), child.text or "", len(params), child.get("unit")))
            else:
                fields.setdefault(tag, _text(child))

        group_id = element.get("group_id") or fields.get("group_id")
        if element.get("group_id") and fields.get("group_id") and element.get("group_id") != fields["group_id"]:
            raise ValueError(f"Conflicting group ID for offer {offer_id}")
        name = fields.get("name")
        if not name:
            raise ValueError(f"Offer {offer_id} has no name")
        shelf = fields.get("categoryId", "")
        if shelf and shelf not in self.categories:
            raise ValueError(f"Offer {offer_id} refers to unknown category {shelf}")
        if group_id:
            if group_id not in self.categories:
                raise ValueError(f"Offer {offer_id} refers to unknown group {group_id}")
            kind, card_id = "software", f"group:{group_id}"
        else:
            kind, card_id = "hardware", f"offer:{offer_id}"

        if card_id not in self.cards:
            category_name = self.categories[group_id][1] if group_id else ""
            self.cards[card_id] = Accumulator(
                kind, category_name or name, group_id if category_name else offer_id,
                "category" if category_name else "feed",
            )
        raw_vendor = fields.get("vendor", "")
        vendor_id = search_text(raw_vendor)
        if vendor_id:
            self.vendors.setdefault(vendor_id, raw_vendor)
        notes = fields.get("sales_notes", "")
        min_qty, max_qty = order_bounds(notes)
        available = _availability(element.get("available"))
        raw_currency, raw_price = fields.get("currencyId", ""), fields.get("price", "")
        currency, minor, price_state = currency_and_price(raw_currency, raw_price)
        is_gpu = kind == "hardware" and self.classify(kind, name, {shelf})[0] == "gpu"
        attrs = normalize_attributes(
            params, name, kind, is_gpu, available=available, sale_notes=notes,
            offer_id=offer_id, version=self.version,
        )
        refs = [_ref("feed", self.version, offer_id, "name")]
        if element.get("available") is not None:
            refs.append(_ref("feed", self.version, offer_id, "available", note=element.get("available")))
        if "price" in fields:
            refs.append(_ref("feed", self.version, offer_id, "price", note=raw_price))
        if "currencyId" in fields:
            refs.append(_ref("feed", self.version, offer_id, "currencyId", note=raw_currency))
        for key in ("url", "vendorCode", "categoryId", "sales_notes"):
            if fields.get(key):
                refs.append(_ref("feed", self.version, offer_id, key))
        if group_id:
            refs.append(_ref("feed", self.version, offer_id, "group_id"))
        offer = Offer(
            offer_id=offer_id, card_id=card_id, name=name, vendor_code=fields.get("vendorCode") or None,
            price=Money(amount_minor=minor, currency=currency) if minor is not None else None,
            price_state=price_state, available=available, url=_safe_url(fields.get("url", "")),
            image_url=_safe_url(fields.get("picture", "")), sales_notes_raw=notes or None,
            attrs=attrs, raw_params=[{"name": p.name, "value": p.value, "index": p.index} for p in params],
            sale_unit="unknown", min_order_quantity=min_qty, max_order_quantity=max_qty,
            source_refs=refs, catalog_version=self.version,
        )
        self.cards[card_id].add(offer, fields, shelf, vendor_id)
        self.conn.execute(
            "INSERT INTO offers VALUES (? ,? ,? ,? ,? ,? ,? ,? ,? ,? ,? ,? ,?)",
            (
                offer_id, card_id, currency, minor, price_state, available, offer.vendor_code,
                raw_currency, raw_price, _json({p.index: p.unit for p in params if p.unit is not None}),
                _json([p.model_dump() for p in offer.raw_params]),
                _json([a.model_dump(exclude_none=True, exclude_defaults=True) for a in attrs]),
                offer.model_dump_json(exclude={"raw_params", "attrs"}, exclude_none=True),
            ),
        )
        self.conn.executemany(
            "INSERT INTO offer_attr_values VALUES (?, ?, ?, ?, ?, ?)",
            (
                (offer_id, a.key, _json(value), a.unit, a.knowledge,
                 next((r.raw_index for r in a.source_refs if r.raw_index is not None), None))
                for a in attrs
                for value in (a.values or ([[a.min_value, a.max_value]] if a.min_value is not None else []))
            ),
        )
        if offer.vendor_code:
            self.alias(search_text(offer.vendor_code), "sku", card_id, "vendorCode")
        self.stats["offers"] += 1
        self.stats[f"{kind}_offers"] += 1
        self.stats[f"price_{price_state}"] += 1
        self.stats["available_offers"] += available is True
        self.stats["raw_params"] += len(params)

    def alias(self, text: str, target_type: str, target_id: str, provenance: str) -> None:
        if text:
            self.conn.execute(
                "INSERT OR IGNORE INTO aliases VALUES (?,?,?,?)",
                (text, target_type, target_id, provenance),
            )

    def store_metadata(self) -> None:
        for category_id, (parent, label) in self.categories.items():
            if parent and parent not in self.categories:
                raise ValueError(f"Category {category_id} refers to unknown parent {parent}")
            self.conn.execute("INSERT INTO categories VALUES (?,?,?)", (category_id, parent, label))
        for singular in ("class", "role"):
            table = "classes" if singular == "class" else "roles"
            self.conn.executemany(
                f"INSERT INTO {table} VALUES (?,?)",
                ((key, _json(data)) for key, data in self.taxonomy[table].items()),
            )
        for key, value in sorted(self.vendors.items()):
            self.conn.execute("INSERT INTO vendors VALUES (?,?)", (key, value))
            self.alias(key, "vendor", key, "vendor")
            for canonical, aliases in self.alias_rules["vendor"]:
                if key == canonical:
                    for alias in aliases:
                        self.alias(search_text(alias), "vendor_alias", key, "aliases")
        for relation in read_resource("relations"):
            model = Relation.model_validate(relation)
            for endpoint in (model.source, model.target):
                collection = "classes" if endpoint.type == "class" else "roles"
                if endpoint.id not in self.taxonomy[collection]:
                    raise ValueError(f"Unknown relation endpoint: {endpoint.id}")
            self.conn.execute(
                "INSERT INTO relations VALUES (?,?)",
                (model.relation_id, model.model_dump_json(exclude_none=True)),
            )

    def store_cards(self) -> None:
        self.store_metadata()
        for card_id, acc in sorted(self.cards.items()):
            class_id, extra_roles, shelf_source = self.classify(acc.kind, acc.name, acc.shelves)
            class_roles = self.classification["class_roles"].get(class_id, [])
            if shelf_source is not None:
                role_ref = _ref("category", self.version, shelf_source, "name")
            else:
                role_ref = _ref(acc.name_source, self.version, acc.name_entity_id, "name")
            roles = [
                Role(role_id=role, verified=True, source_refs=[
                    role_ref, _ref("taxonomy_rule", self.version, role, "classification",
                                   artifact_version=self.taxonomy_version),
                ])
                for role in dict.fromkeys([*class_roles, *extra_roles])
            ]
            if class_id == "backup" and acc.vm_backup_offer:
                roles.append(Role(
                    role_id="backup_virtual_machine", verified=True,
                    source_refs=[_ref("feed", self.version, acc.vm_backup_offer, "name")],
                ))
            facets = [facet.model(key) for key, facet in sorted(acc.facets.items())]
            domestic_shelves = sorted(
                shelf for shelf in acc.shelves
                if re.match(r"российск\w*|отечественн\w*", normalized(self.categories[shelf][1]))
            )
            if domestic_shelves and acc.kind == "software":
                facets.append(Attribute(
                    key="domestic_hint", knowledge="known", values=[True],
                    source_refs=[_ref("category", self.version, shelf, "name") for shelf in domestic_shelves],
                ))
            if acc.kind == "software" and re.search(r"\bsaas\b", normalized(acc.name)):
                facets.append(Attribute(key="saas", knowledge="known", values=[True], source_refs=[role_ref]))

            price_min = price_max = None
            if len(acc.prices) == 1:
                currency, (low, high) = next(iter(acc.prices.items()))
                price_min, price_max = Money(amount_minor=low, currency=currency), Money(amount_minor=high, currency=currency)
            card = Card(
                card_id=card_id, kind=acc.kind, name=acc.name, vendor_ids=sorted(acc.vendors),
                class_id=class_id, roles=roles, shelf_ids=sorted(acc.shelves), description=acc.description,
                image_url=acc.image_url, card_url=acc.url,
                card_url_source=_ref("feed", self.version, acc.name_entity_id, "url") if acc.url else None,
                offer_count=acc.count, available_count=acc.available_count,
                aggregate_price_min=price_min, aggregate_price_max=price_max, facet_values=facets,
                catalog_version=self.version,
            )
            self.conn.execute("INSERT INTO cards VALUES (?,?,?,?,?,?,?,?)", (
                card_id, card.kind, card.name, search_text(card.name), class_id,
                acc.count, acc.available_count, card.model_dump_json(exclude_none=True),
            ))
            self.conn.executemany("INSERT INTO card_roles VALUES (?,?,?,?)", (
                (card_id, role.role_id, role.verified, role.model_dump_json(exclude_none=True)) for role in roles
            ))
            self.conn.executemany("INSERT INTO card_vendors VALUES (?,?)", ((card_id, v) for v in sorted(acc.vendors)))
            self.alias(search_text(card.name), "card", card_id, "name")
            aliases = list(dict.fromkeys(
                search_text(alias) for pattern, values in self.card_alias_patterns if pattern.search(card.name)
                for alias in values
            ))
            for alias in aliases:
                self.alias(alias, "card_alias", card_id, "aliases")
            role_text = " ".join(
                search_text(self.taxonomy["roles"][role.role_id]["label"]) for role in roles
            )
            self.conn.execute("INSERT INTO cards_fts VALUES (?,?,?,?,?,?)", (
                card_id, search_text(card.name), " ".join(sorted(acc.vendors)),
                " ".join(aliases), role_text, search_text(card.description or ""),
            ))
            self.stats[f"{acc.kind}_cards"] += 1


def build_catalog(feed_path: Path, db_path: Path) -> dict:
    """Build and validate a temporary SQLite file before publishing it."""
    feed_path, db_path = feed_path.resolve(strict=True), db_path.resolve()
    if feed_path == db_path:
        raise ValueError("Snapshots must use a different path than the source feed")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = db_path.with_name("manifest.json")
    if manifest_path == feed_path:
        raise ValueError("Manifest path conflicts with the source feed")
    feed_sha = _digest(feed_path)
    tax_sha = taxonomy_digest()
    taxonomy_version = f"taxonomy-{tax_sha[:16]}"
    catalog_fingerprint = hashlib.sha256(
        f"{feed_sha}:{SCHEMA_VERSION}:{NORMALIZER_VERSION}:{tax_sha}".encode()
    ).hexdigest()
    version = f"catalog-{catalog_fingerprint[:20]}"
    fd, temp_name = tempfile.mkstemp(prefix=f".{db_path.name}-", suffix=".tmp", dir=db_path.parent)
    os.close(fd)
    tmp_path = Path(temp_name)
    manifest_tmp = tmp_path.with_suffix(".json")
    conn = sqlite3.connect(tmp_path)
    try:
        conn.executescript(SCHEMA)
        builder = CatalogBuilder(conn, version, taxonomy_version)
        builder.read(feed_path)
        if not builder.stats["offers"]:
            raise ValueError("Expected at least one offer")
        builder.store_cards()
        manifest = {
            "catalog_version": version,
            "catalog_fingerprint": catalog_fingerprint,
            "schema_version": SCHEMA_VERSION,
            "normalizer_version": NORMALIZER_VERSION,
            "taxonomy_version": taxonomy_version,
            "taxonomy_sha256": tax_sha,
            "feed_file": feed_path.name,
            "feed_sha256": feed_sha,
            "feed_date": builder.feed_date,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "counters": {
                **dict(sorted(builder.stats.items())),
                "cards": len(builder.cards),
                "categories": len(builder.categories),
                "vendors": len(builder.vendors),
                "relations": conn.execute("SELECT count(*) FROM relations").fetchone()[0],
            },
            "offline_enrichment": {"llm_calls": 0, "cost": 0, "method": "deterministic_rules_and_reviewed_relations"},
        }
        metadata = {**manifest, "taxonomy": builder.taxonomy}
        conn.executemany("INSERT INTO catalog_meta VALUES (?,?)", (
            (key, _json(value)) for key, value in metadata.items()
        ))
        conn.commit()
        conn.executescript(INDEXES)
        conn.execute("INSERT INTO cards_fts(cards_fts) VALUES ('optimize')")
        conn.execute("INSERT INTO cards_fts(cards_fts) VALUES ('integrity-check')")
        conn.commit()
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("Invalid reference in catalog snapshot")
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Invalid catalog snapshot")
        if conn.execute("SELECT count(*) FROM cards WHERE kind='hardware' AND offer_count<>1").fetchone()[0]:
            raise ValueError("Expected exactly one offer per hardware card")
        conn.close()
        manifest_tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp_path, db_path)
        os.replace(manifest_tmp, manifest_path)
        return manifest
    finally:
        conn.close()
        tmp_path.unlink(missing_ok=True)
        manifest_tmp.unlink(missing_ok=True)

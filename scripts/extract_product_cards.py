"""Извлечение карточек продукта из YML-фида store.softline.ru.

Карточка софта — категория, на которую ссылается group_id офферов.
Оффер без group_id — отдельная карточка (в этом фиде так устроена электроника).
Пустые категории без офферов не попадают в результат.

description заполняется только длинным текстом оффера (от 400 символов).
Короткие строки вида «Язык интерфейса: … • Срок действия: …» — сводка SKU, не текст карточки.
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FEED = ROOT / "docs" / "Mindbox.yml.gz.xml"
DEFAULT_OUT = ROOT / "data" / "product_cards.jsonl"
PROSE_DESCRIPTION_MIN = 400


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def text(el: ET.Element) -> str:
    return (el.text or "").strip()


def clean_name(value: str) -> str:
    return " ".join(value.replace("\u200b", " ").split())


def as_number(value: float) -> int | float:
    if value == int(value):
        return int(value)
    return round(value, 2)


@dataclass
class Card:
    kind: str
    source_id: str
    name: str
    vendors: set[str] = field(default_factory=set)
    vendor_urls: set[str] = field(default_factory=set)
    product_types: set[str] = field(default_factory=set)
    category_ids: set[str] = field(default_factory=set)
    picture: str | None = None
    description: str | None = None
    offer_count: int = 0
    available_count: int = 0
    priced_offer_count: int = 0
    price_min: float | None = None
    price_max: float | None = None
    offers: list[dict] = field(default_factory=list)

    def add_offer(self, offer: dict, category_id: str, prose: str) -> None:
        self.offer_count += 1
        if offer["available"]:
            self.available_count += 1
        if category_id:
            self.category_ids.add(category_id)
        price = offer["price"]
        if price is not None and price > 0:
            self.priced_offer_count += 1
            self.price_min = price if self.price_min is None else min(self.price_min, price)
            self.price_max = price if self.price_max is None else max(self.price_max, price)
        if prose and (self.description is None or len(prose) > len(self.description)):
            self.description = prose
        self.offers.append(offer)


def category_path(categories: dict[str, tuple[str | None, str]], start: str) -> list[dict]:
    path: list[dict] = []
    seen: set[str] = set()
    current: str | None = start
    while current and current not in seen and current in categories:
        seen.add(current)
        parent, name = categories[current]
        path.append({"id": current, "name": name})
        current = parent
    path.reverse()
    return path


def parse_feed(path: Path) -> tuple[dict[str, tuple[str | None, str]], dict[tuple[str, str], Card]]:
    categories: dict[str, tuple[str | None, str]] = {}
    cards: dict[tuple[str, str], Card] = {}
    offers_seen = 0

    for _event, el in ET.iterparse(path, events=("end",)):
        tag = local(el.tag)
        if tag == "category":
            categories[el.attrib.get("id", "")] = (
                el.attrib.get("parentId"),
                clean_name(text(el)),
            )
            el.clear()
        elif tag == "offer":
            offers_seen += 1
            if offers_seen % 50000 == 0:
                print(f"offers: {offers_seen}", file=sys.stderr)
            record = read_offer(el)
            el.clear()
            key, card_name = card_key(record, categories)
            card = cards.get(key)
            if card is None:
                card = Card(kind=key[0], source_id=key[1], name=card_name)
                cards[key] = card
            if record["vendor"]:
                card.vendors.add(record["vendor"])
            if record["vendor_url"]:
                card.vendor_urls.add(record["vendor_url"])
            if record["product_type"]:
                card.product_types.add(record["product_type"])
            if card.picture is None and record["picture"]:
                card.picture = record["picture"]
            card.add_offer(record["offer"], record["category_id"], record["prose"])
    print(f"offers: {offers_seen}", file=sys.stderr)
    return categories, cards


def read_offer(el: ET.Element) -> dict:
    fields: dict[str, str] = {}
    product_type = ""
    for child in el:
        child_tag = local(child.tag)
        if child_tag == "param":
            if child.attrib.get("name") == "Тип продукта" and not product_type:
                product_type = text(child)
            continue
        if child_tag not in fields:
            fields[child_tag] = text(child)

    raw_price = fields.get("price", "")
    price: float | None
    try:
        price = float(raw_price.replace(" ", "").replace(",", ".")) if raw_price else None
    except ValueError:
        price = None

    description = fields.get("description", "")
    prose = description if len(description) >= PROSE_DESCRIPTION_MIN else ""
    group_id = el.attrib.get("group_id") or fields.get("group_id") or ""
    offer = {
        "id": el.attrib.get("id", ""),
        "name": fields.get("name", ""),
        "price": None if price is None else as_number(price),
        "available": el.attrib.get("available") == "true",
        "url": fields.get("url", ""),
    }
    vendor_code = fields.get("vendorCode", "")
    if vendor_code:
        offer["vendor_code"] = vendor_code
    return {
        "group_id": group_id,
        "category_id": fields.get("categoryId", ""),
        "vendor": fields.get("vendor", ""),
        "vendor_url": fields.get("vendorUrl", ""),
        "picture": fields.get("picture", ""),
        "product_type": product_type,
        "prose": prose,
        "offer": offer,
        "offer_name": fields.get("name", ""),
    }


def card_key(record: dict, categories: dict[str, tuple[str | None, str]]) -> tuple[tuple[str, str], str]:
    group_id = record["group_id"]
    if group_id:
        name = categories.get(group_id, (None, ""))[1] or record["offer_name"]
        return ("group", group_id), name
    return ("offer", record["offer"]["id"]), record["offer_name"]


def to_json(card: Card, categories: dict[str, tuple[str | None, str]]) -> dict:
    if card.kind == "group":
        breadcrumb_start = card.source_id
    else:
        breadcrumb_start = next(iter(card.category_ids), "")
    offers = sorted(card.offers, key=lambda item: int(item["id"]) if item["id"].isdigit() else item["id"])
    return {
        "id": f"{card.kind}:{card.source_id}",
        "kind": card.kind,
        "name": card.name,
        "vendors": sorted(card.vendors),
        "vendor_urls": sorted(card.vendor_urls),
        "product_types": sorted(card.product_types),
        "categories": category_path(categories, breadcrumb_start),
        "category_ids": sorted(card.category_ids, key=lambda item: int(item) if item.isdigit() else item),
        "picture": card.picture,
        "description": card.description,
        "offer_count": card.offer_count,
        "available_count": card.available_count,
        "priced_offer_count": card.priced_offer_count,
        "price_min": None if card.price_min is None else as_number(card.price_min),
        "price_max": None if card.price_max is None else as_number(card.price_max),
        "offers": offers,
    }


def write_cards(
    cards: dict[tuple[str, str], Card],
    categories: dict[str, tuple[str | None, str]],
    output: Path,
) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(
        cards.values(),
        key=lambda card: (card.kind, int(card.source_id) if card.source_id.isdigit() else card.source_id),
    )
    stats: dict = defaultdict(int)
    sample_with_text: str | None = None
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        for card in ordered:
            record = to_json(card, categories)
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
            stats["cards"] += 1
            stats[f"kind_{card.kind}"] += 1
            stats["offers"] += card.offer_count
            if card.description:
                stats["with_description"] += 1
                if sample_with_text is None:
                    sample_with_text = f"{record['id']} {card.name}"
            if card.priced_offer_count:
                stats["with_price"] += 1
            if len(card.category_ids) > 1:
                stats["mixed_category"] += 1
            for product_type in card.product_types:
                stats[f"type_{product_type}"] += 1
    stats["sample_with_text"] = sample_with_text or ""
    stats["bytes"] = output.stat().st_size
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Собрать карточки продукта из YML-фида")
    parser.add_argument("feed", nargs="?", type=Path, default=DEFAULT_FEED)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    if not args.feed.is_file():
        raise SystemExit(f"фид не найден: {args.feed}")

    categories, cards = parse_feed(args.feed)
    stats = write_cards(cards, categories, args.output)
    print(f"output: {args.output}")
    print(f"cards: {stats['cards']}")
    print(f"group cards: {stats['kind_group']}")
    print(f"offer cards: {stats['kind_offer']}")
    print(f"offers covered: {stats['offers']}")
    print(f"cards with price: {stats['with_price']}")
    print(f"cards with description: {stats['with_description']}")
    print(f"cards with several category ids: {stats['mixed_category']}")
    print(f"bytes: {stats['bytes']}")
    if stats["sample_with_text"]:
        print(f"description sample: {stats['sample_with_text']}")


if __name__ == "__main__":
    main()

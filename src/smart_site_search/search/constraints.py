"""Keep stated customer conditions in the main results, with one disclosed near-miss shelf."""

from dataclasses import dataclass

from ..catalog.normalization import NUMERIC_FILTER_KEYS
from ..contracts.api import ResultItem
from .verify import is_budget_ceiling

PRICE_STRETCH = (
    "Товара в вашем ценовом диапазоне нет, но можем предложить решения до 40% выше указанной цены."
)
PRICE_EMPTY = "Товара в вашем ценовом диапазоне нет."
CATALOG_EMPTY = "В каталоге нет товара, который выполняет все указанные условия."
UNVERIFIED = "Указанные условия по каталогу подтвердить не удалось."
KIT_BUDGET = "Бюджет комплекта по данным каталога не проверяется."
NUMERIC_SHELF = NUMERIC_FILTER_KEYS - {"price_minor"}


@dataclass(frozen=True)
class ConstraintDecision:
    items: list[ResultItem]
    notes: list[str]
    price_unmet: bool
    unmet_filter_ids: list[str]


def closest_note(source: str) -> str:
    return f"Точного совпадения по условию «{source}» нет, можем предложить ближайшие варианты."


def apply_explicit_constraints(results: list[ResultItem], filters) -> ConstraintDecision:
    related = [item for item in results if item.group == "related"]
    primary = [item for item in results if item.group != "related"]
    enforced = [item for item in filters if item.strength == "require" and item.key != "total_price_minor"]
    notes = [KIT_BUDGET] if any(item.key == "total_price_minor" and item.strength == "require" for item in filters) else []
    if not enforced:
        return ConstraintDecision([*primary, *related], notes, False, [])

    classified = [_classify(item, enforced) for item in primary]
    fitting = [item for item, kind, _miss in classified if kind == "fit"]
    if fitting:
        return ConstraintDecision([*fitting, *related], notes, False, [])

    singles = [(item, missed[0]) for item, kind, missed in classified if kind == "single" and missed]
    shelf, shelf_filter = _shelf(singles, filters)
    if shelf is not None and shelf_filter is not None:
        if _is_price(shelf_filter):
            notes.insert(0, PRICE_STRETCH)
            unmet = []
        else:
            notes.insert(0, closest_note(shelf_filter.source_text or shelf_filter.key))
            unmet = [shelf_filter.filter_id]
        return ConstraintDecision([*shelf, *related], notes, _price_unmet(primary, shelf, filters), unmet)

    unverified = [item for item, kind, _miss in classified if kind == "unverified"]
    mismatches = [missed for _item, kind, misses in classified if kind in {"single", "drop"} for missed in misses]
    if classified and all(kind == "unverified" for _item, kind, _miss in classified):
        notes.insert(0, UNVERIFIED)
        return ConstraintDecision([*unverified, *related], notes, False, [])
    if any(not _is_price(missed) for missed in mismatches):
        notes.insert(0, CATALOG_EMPTY)
    elif _known_price_miss(primary, filters):
        notes.insert(0, PRICE_EMPTY)
    elif primary:
        notes.insert(0, CATALOG_EMPTY)
    unmet = list(dict.fromkeys(missed.filter_id for missed in mismatches if not _is_price(missed)))
    return ConstraintDecision(related, notes, _price_unmet(primary, [], filters), unmet)


def _classify(item: ResultItem, enforced):
    misses = []
    unknowns = []
    checks = {check.filter_id: check for check in (*item.matched_conditions, *item.unmet_conditions)}
    for filter_ in enforced:
        if _is_price(filter_):
            if item.budget_status == "within":
                continue
            if item.budget_status == "over":
                misses.append(filter_)
            else:
                unknowns.append(filter_)
            continue
        check = checks.get(filter_.filter_id)
        if check is None or check.status == "unknown":
            unknowns.append(filter_)
        elif check.status == "mismatch":
            misses.append(filter_)
    if not misses and not unknowns:
        return item, "fit", []
    if len(misses) == 1 and not unknowns:
        return item, "single", misses
    if not misses:
        return item, "unverified", []
    return item, "drop", misses


def _shelf(singles, filters):
    grouped = {}
    for item, missed in singles:
        grouped.setdefault(missed.filter_id, (missed, []))[1].append(item)
    eligible = []
    for missed, cards in grouped.values():
        if _is_price(missed):
            band = [card for card in cards if _within_price_band(card, filters)]
            if band:
                eligible.append((missed, band, 0))
            continue
        if missed.key not in NUMERIC_SHELF:
            continue
        measured = []
        for card in cards:
            gap = _gap(missed, _check(card, missed))
            if gap is not None:
                measured.append((card, gap))
        if not measured:
            continue
        best = min(gap for _card, gap in measured)
        eligible.append((missed, [card for card, gap in measured if gap == best], best))
    if not eligible:
        return None, None
    price = next((choice for choice in eligible if _is_price(choice[0])), None)
    if price is not None:
        return price[1], price[0]

    def rank(choice):
        missed, _cards, gap = choice
        target = missed.values[0] if missed.values and type(missed.values[0]) is int else 0
        return (gap / (abs(target) or 1), missed.filter_id)

    missed, cards, _gap_value = min(eligible, key=rank)
    return cards, missed


def _gap(filter_, check) -> int | None:
    if check is None or check.status != "mismatch":
        return None
    numbers = [value for value in check.actual_values if type(value) is int]
    targets = [value for value in filter_.values if type(value) is int]
    if not numbers or not targets:
        return None
    if filter_.op == "gte":
        gaps = [targets[0] - value for value in numbers if value < targets[0]]
    elif filter_.op == "lte":
        gaps = [value - targets[0] for value in numbers if value > targets[0]]
    elif filter_.op == "eq":
        gaps = [abs(value - targets[0]) for value in numbers]
    elif len(targets) > 1:
        low, high = targets[0], targets[1]
        gaps = [low - value if value < low else value - high for value in numbers if value < low or value > high]
    else:
        return None
    return min(gaps) if gaps else None


def _check(item: ResultItem, filter_):
    return next((check for check in (*item.matched_conditions, *item.unmet_conditions)
                 if check.filter_id == filter_.filter_id), None)


def _within_price_band(item: ResultItem, filters) -> bool:
    if item.budget_status != "over" or item.price is None:
        return False
    checked = False
    for ceiling in filters:
        if not _is_price(ceiling):
            continue
        cap = ceiling.values[-1] if ceiling.values else None
        if type(cap) is not int or item.price.currency != ceiling.currency:
            return False
        if item.price.amount_minor * 5 > cap * 7:
            return False
        checked = True
    return checked


def _is_price(filter_) -> bool:
    return is_budget_ceiling(filter_) and filter_.key == "price_minor"


def _known_price_miss(primary, filters) -> bool:
    return any(_is_price(item) for item in filters) and any(item.budget_status == "over" for item in primary)


def _price_unmet(primary, shown, filters) -> bool:
    if not any(_is_price(item) for item in filters):
        return False
    if not any(item.budget_status == "over" for item in primary):
        return False
    return not any(item.budget_status == "within" for item in shown)

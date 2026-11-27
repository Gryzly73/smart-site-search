import re
from time import monotonic
from typing import Iterable

from ..catalog.normalization import NUMERIC_FILTER_KEYS
from ..contracts.catalog import Attribute, Card, Money, Offer
from ..contracts.intent import Filter, QueryState
from ..contracts.search import BudgetStatus, Candidate, ConditionCheck, OfferCheck, RankFactors, VerifiedCandidate


def is_budget_ceiling(filter_: Filter) -> bool:
    return (filter_.key in {"price_minor", "total_price_minor"} and filter_.strength == "require"
            and filter_.op in {"lte", "between"})


BUDGET_PRIORITY = {"within": 0, "over": 1, "unconfirmed": 2, None: 0}


def budget_fit(filters, offer: OfferCheck) -> tuple[BudgetStatus | None, Money | None]:
    ceilings = [f for f in filters if is_budget_ceiling(f)]
    if not ceilings:
        return None, None
    checks = {check.filter_id: check for check in offer.conditions}
    budget = [f for f in ceilings if f.filter_id in checks]
    if not budget:
        return None, None
    budget_ids = {f.filter_id for f in budget}
    other = [(f, checks[f.filter_id]) for f in filters if f.strength != "prefer"
             and f.filter_id in checks and f.filter_id not in budget_ids]
    if all(checks[f.filter_id].status == "match" for f in budget):
        return ("unconfirmed" if any(check.status == "unknown" for _, check in other) else "within"), None
    overages = []
    for filter_ in budget:
        check = checks[filter_.filter_id]
        if check.status == "match":
            continue
        price = offer.quantity_total if filter_.key == "total_price_minor" else offer.price
        cap = filter_.values[-1]
        if (check.status != "mismatch" or price is None or price.currency != filter_.currency
                or type(cap) is not int or price.amount_minor <= cap):
            return "unconfirmed", None
        overages.append(Money(amount_minor=price.amount_minor - cap, currency=price.currency))
    if all(check.status == ("mismatch" if f.strength == "exclude" else "match") for f, check in other):
        return "over", max(overages, key=lambda amount: amount.amount_minor)
    return "unconfirmed", None


def _check(filter_: Filter, card: Card, offer: Offer) -> ConditionCheck:
    values = {
        "card_id": [card.card_id],
        "kind": [card.kind],
        "vendor_id": card.vendor_ids,
        "class_id": [card.class_id],
        "role_id": [role.role_id for role in card.roles if role.verified],
        "available": [] if offer.available is None else [offer.available],
        "price_minor": [] if offer.price is None else [offer.price.amount_minor],
    }
    fields = {attribute.key: attribute for attribute in card.facet_values
              if attribute.key in {"domestic_hint", "saas"}}
    fields.update((attribute.key, attribute) for attribute in offer.attrs)
    attribute: Attribute | None = fields.get(filter_.key)
    actual = values.get(filter_.key, attribute.values if attribute else [])
    refs = attribute.source_refs if attribute else [
        ref for ref in offer.source_refs
        if ref.field == {"available": "available", "price_minor": "price"}.get(filter_.key)
    ]
    if filter_.key == "role_id":
        refs = [ref for role in card.roles if role.verified for ref in role.source_refs]
    result = dict(filter_id=filter_.filter_id, actual_values=actual, source_refs=refs)
    if filter_.key == "total_price_minor":
        return ConditionCheck(**result, status="unknown", reason_code="unknown_quantity_basis")
    if filter_.key == "price_minor" and (
        offer.price is None or offer.price.currency != filter_.currency
    ):
        return ConditionCheck(**result, status="unknown", reason_code="unknown_price")
    if filter_.key == "price_minor" and is_budget_ceiling(filter_) and (offer.min_order_quantity or 1) > 1:
        return ConditionCheck(**result, status="unknown", reason_code="unknown_quantity_basis")
    if filter_.key == "term_months" and not actual and (perpetual := fields.get("perpetual")) and (
        perpetual.knowledge == "known" and perpetual.values == [True]
    ):
        result["source_refs"] = perpetual.source_refs
        return ConditionCheck(**result, status="mismatch", reason_code="value_mismatch")
    if attribute and attribute.knowledge != "known":
        reason = "conflicting_values" if attribute.knowledge == "conflicting" else "missing_attribute"
        return ConditionCheck(**result, status="unknown", reason_code=reason)
    if attribute and filter_.unit and attribute.unit != filter_.unit:
        return ConditionCheck(**result, status="unknown", reason_code="unsupported_condition")

    query = filter_.values
    matched = None
    if filter_.op == "eq" and type(query[0]) is int and attribute is not None and (
        attribute.min_value is not None and attribute.max_value is not None
    ):
        matched = attribute.min_value <= query[0] <= attribute.max_value
    elif actual:
        comparable = [(a, b) for a in actual for b in query if type(a) is type(b)]
        if filter_.op in {"eq", "in"}:
            matched = any(_normalized(a) == _normalized(b) for a, b in comparable)
        elif type(query[0]) is int:
            numbers = [value for value in actual if type(value) is int]
            if numbers:
                if filter_.op == "gte":
                    matched = any(value >= query[0] for value in numbers)
                elif filter_.op == "lte":
                    matched = any(value <= query[0] for value in numbers)
                else:
                    matched = any(query[0] <= value <= query[1] for value in numbers)
    if matched is None:
        return ConditionCheck(**result, status="unknown", reason_code="missing_attribute")
    return ConditionCheck(**result, status="match" if matched else "mismatch",
                          reason_code="value_match" if matched else "value_mismatch")


def _normalized(value):
    return value.casefold().strip() if isinstance(value, str) else value


def numeric_distance(filters, checks) -> int:
    """Gap to a required number. Matches and empty values add nothing."""
    by_id = {check.filter_id: check for check in checks}
    total = 0
    for item in filters:
        if item.strength != "require" or item.key not in NUMERIC_FILTER_KEYS or item.op not in {
            "eq", "gte", "lte", "between",
        }:
            continue
        check = by_id.get(item.filter_id)
        if check is None or check.status != "mismatch":
            continue
        numbers = [value for value in check.actual_values if type(value) is int]
        targets = [value for value in item.values if type(value) is int]
        if not numbers or not targets:
            continue
        if item.op == "gte":
            gaps = [targets[0] - value for value in numbers if value < targets[0]]
        elif item.op == "lte":
            gaps = [value - targets[0] for value in numbers if value > targets[0]]
        elif item.op == "eq":
            gaps = [abs(value - targets[0]) for value in numbers]
        else:
            low, high = targets[0], targets[1] if len(targets) > 1 else targets[0]
            gaps = [low - value if value < low else value - high
                    for value in numbers if value < low or value > high]
        if gaps:
            total += min(gaps)
    return total


def _offer_check(candidate, card, offer, query, filters):
    checks = [_check(filter_, card, offer) for filter_ in filters]
    rejected = any(filter_.strength == "exclude" and check.status == "match"
                   for filter_, check in zip(filters, checks))
    mismatches = sum(filter_.strength == "require" and check.status == "mismatch"
                     for filter_, check in zip(filters, checks))
    unknown = sum(filter_.strength != "prefer" and check.status == "unknown"
                  for filter_, check in zip(filters, checks))
    prefers = sum(filter_.strength == "prefer" and check.status == "match"
                  for filter_, check in zip(filters, checks))
    if "backup_virtual_machine" in query.role_ids or (
        "backup_restore" in query.role_ids and re.search(r"\b(?:виртуал[а-я]*|virtual machine|vm)\b",
                                                          query.goal.casefold())
    ):
        prefers += any(attr.key == "platform" and attr.knowledge == "known"
                       and bool({"virtual_machine", "virtualization"}.intersection(attr.values))
                       for attr in offer.attrs)
    available = {True: 0, None: 1, False: 2}[offer.available]
    level = "partial" if (mismatches or unknown or query.unresolved_constraints
                         or candidate.relevance != "confirmed") else "full"
    if rejected:
        level = "rejected"
    checked = OfferCheck(
        offer_id=offer.offer_id, card_id=card.card_id, conditions=checks,
        required_match=level, price=offer.price, available=offer.available,
        url=offer.url, source_refs=offer.source_refs,
    )
    price = offer.price.amount_minor if offer.price is not None else float("inf")
    license_types = {value for attr in offer.attrs if attr.key == "license_type" and attr.knowledge == "known"
                     for value in attr.values}
    add_on = bool(license_types.intersection({"renewal", "upgrade", "support", "maintenance"}))
    add_on |= bool(re.search(r"\bcross[ -]?grade\b", offer.name.casefold()))
    if offer.sale_unit != "device" and not license_types and (
        "сопровождени" in offer.name.casefold() or "техподдержк" in offer.name.casefold()
    ):
        add_on = True
    budget_status, _ = budget_fit(filters, checked)
    key = ({"full": 0, "partial": 1, "rejected": 2}[level], BUDGET_PRIORITY[budget_status], mismatches, unknown,
           -prefers, available, add_on, numeric_distance(filters, checks), price, offer.offer_id)
    return key, checked


def verify_card(candidate: Candidate, card: Card, offers: Iterable[Offer], query: QueryState,
                deadline: float) -> VerifiedCandidate:
    checked = []
    complete = True
    filters = [
        item for item in query.filters
        if item.component_id is None or item.component_id == candidate.component_id
    ]
    excluded = card.card_id in query.excluded_card_ids or bool(
        set(card.vendor_ids).intersection(query.excluded_vendor_ids)
    )
    if not excluded and candidate.relevance != "insufficient":
        for offer in offers:
            if monotonic() >= deadline:
                complete = False
                break
            if offer.card_id == card.card_id:
                key, check = _offer_check(candidate, card, offer, query, filters)
                if check.required_match != "rejected":
                    checked.append((key, check))
    checked.sort(key=lambda pair: pair[0])
    key, selected = checked[0] if checked else (None, None)
    alternatives = [offer for _, offer in checked[1:3]]
    if selected and selected.required_match == "partial":
        selected_status, _ = budget_fit(filters, selected)
        if selected_status in {"within", "over"}:
            opposite = "over" if selected_status == "within" else "within"
            extra = next((offer for _, offer in checked[1:] if budget_fit(filters, offer)[0] == opposite), None)
            if extra is not None:
                alternatives = [extra, *(offer for offer in alternatives if offer.offer_id != extra.offer_id)][:2]
    identity_rank = min(
        ({"exact": 0, "alias": 1, "fuzzy": 2, "role": 3, "fts": 4}[channel]
         for channel in candidate.channels), default=5,
    )
    return VerifiedCandidate(
        candidate=candidate,
        match_level=selected.required_match if selected else "rejected",
        selected_offer=selected,
        alternate_offers=alternatives,
        checks_complete_for_card=complete,
        reason_codes=[] if selected else ["excluded" if excluded else "no_matching_offer"],
        rank_factors=RankFactors(
            relevance_tier={"confirmed": 0, "hypothesis": 1, "insufficient": 2}[candidate.relevance],
            identity_rank=identity_rank, preference_matches=-key[4] if key else 0,
            availability_rank=key[5] if key else 2,
            price_minor=selected.price.amount_minor if selected and selected.price else None,
        ),
    )

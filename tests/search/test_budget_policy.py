from smart_site_search.contracts.api import ResultItem
from smart_site_search.contracts.catalog import Money
from smart_site_search.contracts.intent import Filter
from smart_site_search.contracts.search import ConditionCheck
from smart_site_search.search.constraints import (
    CATALOG_EMPTY, KIT_BUDGET, PRICE_EMPTY, PRICE_STRETCH, UNVERIFIED, apply_explicit_constraints,
    closest_note,
)


def ceiling(amount):
    return Filter(filter_id="price", key="price_minor", op="lte", values=[amount], strength="require",
                  scope="topic", currency="RUB", source_turn_id="1", source_text="до лимита")


def spec(filter_id, key, op, value, text):
    return Filter(filter_id=filter_id, key=key, op=op, values=[value], strength="require",
                  scope="topic", source_turn_id="1", source_text=text)


def condition(filter_id, status, actual):
    return ConditionCheck(filter_id=filter_id, status=status,
                          reason_code="value_match" if status == "match" else "value_mismatch",
                          actual_values=[] if actual is None else [actual], source_refs=[])


def item(card_id, *, group="primary_partial", status="over", amount=130, matched=(), unmet=()):
    return ResultItem(
        card_id=f"offer:{card_id}", name=card_id, vendor_names=[], kind="hardware", class_id="hardware",
        group=group, match_level="full" if group == "primary_full" else "partial",
        component_ids=[], selected_offer_id=card_id,
        price=None if amount is None else Money(amount_minor=amount, currency="RUB"),
        price_label="", available=True, availability_label="", url=None, url_kind="none",
        matched_conditions=list(matched), unmet_conditions=list(unmet), reason="", caveats=[],
        source_refs=[], compatibility_status="not_verified", budget_status=status,
    )


def ids(decision):
    return [card.card_id for card in decision.items]


def test_price_ceiling_keeps_only_items_inside_the_budget():
    decision = apply_explicit_constraints([
        item("within", status="within", amount=80, group="primary_full"),
        item("over", amount=130),
        item("unknown", status="unconfirmed", amount=None),
        item("extra", group="related", status=None, amount=500),
    ], [ceiling(100)])
    assert ids(decision) == ["offer:within", "offer:extra"]
    assert decision.notes == []
    assert decision.price_unmet is False


def test_price_ceiling_offers_a_separate_shelf_up_to_forty_percent():
    decision = apply_explicit_constraints([
        item("close", amount=140),
        item("far", amount=141),
        item("extra", group="related", status=None, amount=900),
    ], [ceiling(100)])
    assert ids(decision) == ["offer:close", "offer:extra"]
    assert decision.notes == [PRICE_STRETCH]
    assert decision.price_unmet is True


def test_price_ceiling_hides_items_beyond_forty_percent():
    decision = apply_explicit_constraints([
        item("far", amount=141),
        item("extra", group="related", status=None, amount=900),
    ], [ceiling(100)])
    assert ids(decision) == ["offer:extra"]
    assert decision.notes == [PRICE_EMPTY]
    assert decision.price_unmet is True


def test_results_without_a_stated_constraint_stay_unchanged():
    cards = [item("over", amount=500, status=None)]
    decision = apply_explicit_constraints(cards, [])
    assert decision.items == cards
    assert decision.notes == []


def test_minimum_memory_shelf_keeps_the_closest_smaller_values():
    memory = spec("vram", "vram_gb", "gte", 48, "от 48 ГБ")
    decision = apply_explicit_constraints([
        item("near", status=None, amount=None, unmet=[condition("vram", "mismatch", 40)]),
        item("far", status=None, amount=None, unmet=[condition("vram", "mismatch", 16)]),
        item("match", status=None, amount=None, group="primary_full", matched=[condition("vram", "match", 64)]),
    ], [memory])
    assert ids(decision) == ["offer:match"]
    assert decision.notes == []

    shelf = apply_explicit_constraints([
        item("near", status=None, amount=None, unmet=[condition("vram", "mismatch", 40)]),
        item("far", status=None, amount=None, unmet=[condition("vram", "mismatch", 16)]),
        item("other", status=None, amount=None, unmet=[condition("vram", "mismatch", 40)]),
    ], [memory])
    assert ids(shelf) == ["offer:near", "offer:other"]
    assert shelf.notes == [closest_note("от 48 ГБ")]
    assert shelf.unmet_filter_ids == ["vram"]


def test_exact_term_shelf_accepts_the_closest_value_on_either_side():
    term = spec("term", "term_months", "eq", 12, "на год")
    decision = apply_explicit_constraints([
        item("short", status=None, amount=None, unmet=[condition("term", "mismatch", 6)]),
        item("long", status=None, amount=None, unmet=[condition("term", "mismatch", 18)]),
        item("far", status=None, amount=None, unmet=[condition("term", "mismatch", 36)]),
    ], [term])
    assert ids(decision) == ["offer:short", "offer:long"]
    assert decision.notes == [closest_note("на год")]


def test_a_card_that_misses_two_conditions_stays_off_the_shelf():
    decision = apply_explicit_constraints([
        item("both", amount=120, unmet=[condition("vram", "mismatch", 40)]),
        item("price", amount=120, matched=[condition("vram", "match", 64)]),
        item("memory", status="within", amount=80, unmet=[condition("vram", "mismatch", 40)]),
    ], [ceiling(100), spec("vram", "vram_gb", "gte", 48, "от 48 ГБ")])
    assert ids(decision) == ["offer:price"]
    assert decision.notes == [PRICE_STRETCH]
    assert decision.unmet_filter_ids == []


def test_category_mismatch_has_no_nearest_shelf():
    organization = spec("org", "organization_type", "eq", "commercial", "коммерческая организация")
    decision = apply_explicit_constraints([
        item("edu", status=None, amount=None, unmet=[condition("org", "mismatch", "academic")]),
        item("extra", group="related", status=None, amount=None),
    ], [organization])
    assert ids(decision) == ["offer:extra"]
    assert decision.notes == [CATALOG_EMPTY]
    assert decision.unmet_filter_ids == ["org"]


def test_fitting_category_hides_the_mismatch():
    organization = spec("org", "organization_type", "eq", "commercial", "коммерческая организация")
    decision = apply_explicit_constraints([
        item("commercial", status=None, amount=None, group="primary_full",
             matched=[condition("org", "match", "commercial")]),
        item("edu", status=None, amount=None, unmet=[condition("org", "mismatch", "academic")]),
    ], [organization])
    assert ids(decision) == ["offer:commercial"]
    assert decision.notes == []


def test_kit_budget_stays_visible_when_the_total_cannot_be_checked():
    total = Filter(filter_id="total", key="total_price_minor", op="lte", values=[100], strength="require",
                   scope="topic", currency="RUB", source_turn_id="1", source_text="за весь комплект")
    cards = [item("server", status=None, amount=500, group="primary_full")]
    decision = apply_explicit_constraints(cards, [total])
    assert decision.items == cards
    assert decision.notes == [KIT_BUDGET]
    assert decision.price_unmet is False


def test_unknown_characteristic_is_not_reported_as_an_empty_catalog():
    memory = spec("vram", "vram_gb", "gte", 48, "от 48 ГБ")
    cards = [item("unknown", status=None, amount=None, unmet=[condition("vram", "unknown", None)])]
    cards[0].unmet_conditions[0] = cards[0].unmet_conditions[0].model_copy(update={
        "status": "unknown", "reason_code": "missing_attribute",
    })
    decision = apply_explicit_constraints(cards, [memory])
    assert ids(decision) == ["offer:unknown"]
    assert decision.notes == [UNVERIFIED]

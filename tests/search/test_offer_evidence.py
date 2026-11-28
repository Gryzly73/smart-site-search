from time import monotonic

from smart_site_search.contracts.catalog import Attribute, Card, Money, Offer
from smart_site_search.contracts.intent import Filter, QueryState
from smart_site_search.contracts.search import Candidate
from smart_site_search.search import verify_card


def test_aggregate_term_does_not_prove_another_offers_license():
    term = Attribute(key="term_months", knowledge="known", values=[24], source_refs=[])
    card = Card(card_id="group:1", kind="software", name="Test", vendor_ids=[],
                class_id="software", roles=[], shelf_ids=[], offer_count=2, available_count=2,
                facet_values=[term], catalog_version="test")
    candidate = Candidate(card_id=card.card_id, channels=["exact"], channel_ranks=[],
                          matched_role_ids=[], evidence=[], relevance="confirmed")
    cheap = Offer(offer_id="1", card_id=card.card_id, name="Test", price_state="known",
                  price=Money(amount_minor=500, currency="RUB"), available=True, attrs=[],
                  sale_unit="license", raw_params=[], source_refs=[], catalog_version="test")
    expensive = cheap.model_copy(update={
        "offer_id": "2", "price": Money(amount_minor=2500, currency="RUB"), "attrs": [term],
    })
    state = QueryState(goal="Test", mode="direct", filters=[
        Filter(filter_id="term", key="term_months", op="eq", values=[24], strength="require",
               scope="topic", source_turn_id="1", source_text="24 месяца"),
        Filter(filter_id="price", key="price_minor", op="lte", values=[1000], strength="require",
               scope="topic", currency="RUB", source_turn_id="1", source_text="до 10 руб."),
    ])
    checked = verify_card(candidate, card, [cheap, expensive], state, monotonic() + 2)
    assert checked.match_level == "partial"
    assert checked.selected_offer.offer_id == "2"
    assert [condition.status for condition in checked.selected_offer.conditions] == ["match", "mismatch"]
    assert checked.alternate_offers[0].offer_id == "1"


def test_budget_comparison_keeps_verified_alternatives_on_the_same_card():
    from smart_site_search.search.verify import budget_fit

    card, candidate = _hardware("offer:gpu", [])
    under = _gpu_offer("12gb", card.card_id, 12, 900)
    others_under = [_gpu_offer(str(memory), card.card_id, memory, 800) for memory in (2, 4, 8)]
    over = _gpu_offer("24gb", card.card_id, 24, 1300)
    unknown = under.model_copy(update={"offer_id": "unknown", "attrs": []})
    maximum = _number("price", "price_minor", "lte", 1000, "до 10 ₽").model_copy(update={"currency": "RUB"})
    state = QueryState(goal="GPU", mode="direct", filters=[
        _number("vram", "vram_gb", "gte", 24, "от 24 ГБ"), maximum,
    ])
    checked = verify_card(candidate, card, [under, *others_under, over, unknown], state, monotonic() + 2)
    assert checked.selected_offer.offer_id == "24gb"
    assert budget_fit(state.filters, checked.selected_offer) == (
        "over", Money(amount_minor=300, currency="RUB"),
    )


def test_budget_with_unconfirmed_quantity_cannot_be_a_full_match():
    card, candidate = _hardware("offer:gpu", [])
    offer = _gpu_offer("pack", card.card_id, 24, 900).model_copy(update={"min_order_quantity": 4})
    maximum = _number("price", "price_minor", "lte", 1000, "бюджет 10 ₽").model_copy(update={"currency": "RUB"})
    state = QueryState(goal="GPU", mode="direct", filters=[maximum])
    checked = verify_card(candidate, card, [offer], state, monotonic() + 2)
    assert checked.match_level == "partial"
    condition, = checked.selected_offer.conditions
    assert (condition.status, condition.reason_code) == ("unknown", "unknown_quantity_basis")


def test_over_budget_match_stays_visible_when_approximate_matches_fill_the_page():
    from smart_site_search.search.engine import _reserve_over_budget

    maximum = _number("price", "price_minor", "lte", 1000, "до 10 ₽").model_copy(update={"currency": "RUB"})
    state = QueryState(goal="GPU", mode="direct", filters=[
        _number("vram", "vram_gb", "gte", 24, "от 24 ГБ"), maximum,
    ])
    items = []
    for index in range(6):
        card, candidate = _hardware(f"offer:gpu-{index}", [])
        memory, price = (24, 1300) if index == 5 else (12, 900)
        offer = _gpu_offer(str(index), card.card_id, memory, price)
        items.append(verify_card(candidate, card, [offer], state, monotonic() + 2))
    page = _reserve_over_budget(items, state, 4)
    assert [item.selected_offer.offer_id for item in page] == ["0", "1", "2", "5", "3", "4"]


def _hardware(card_id, offers):
    card = Card(card_id=card_id, kind="hardware", name="Видеокарта", vendor_ids=[],
                class_id="gpu", roles=[], shelf_ids=[], offer_count=len(offers),
                available_count=sum(offer.available is True for offer in offers),
                facet_values=[], catalog_version="test")
    candidate = Candidate(card_id=card_id, channels=["role"], channel_ranks=[],
                          matched_role_ids=["gpu"], evidence=[], relevance="confirmed")
    return card, candidate


def _gpu_offer(offer_id, card_id, memory, price, available=True):
    memory_attr = Attribute(key="vram_gb", knowledge="known", values=[memory], unit="GB", source_refs=[])
    return Offer(offer_id=offer_id, card_id=card_id, name=f"Видеокарта {memory} ГБ", price_state="known",
                 price=Money(amount_minor=price, currency="RUB"), available=available, attrs=[memory_attr],
                 sale_unit="device", raw_params=[], source_refs=[], catalog_version="test")


def _number(filter_id, key, op, value, text):
    return Filter(filter_id=filter_id, key=key, op=op, values=[value], strength="require",
                  scope="topic", source_turn_id="1", source_text=text)


def test_closer_vram_beats_cheaper_far_miss():
    card, candidate = _hardware("offer:gpu", [])
    cheap = _gpu_offer("2gb", card.card_id, 2, 100)
    closer = _gpu_offer("12gb", card.card_id, 12, 5000)
    state = QueryState(goal="от 48 ГБ", mode="expert", filters=[
        _number("vram", "vram_gb", "gte", 48, "от 48 ГБ"),
        _number("stock", "available", "eq", True, "в наличии"),
    ])
    checked = verify_card(candidate, card, [cheap, closer], state, monotonic() + 2)
    assert checked.match_level == "partial"
    assert checked.selected_offer.offer_id == "12gb"


def test_closer_term_beats_cheaper_far_miss():
    short = Attribute(key="term_months", knowledge="known", values=[1], source_refs=[])
    year = Attribute(key="term_months", knowledge="known", values=[12], source_refs=[])
    card = Card(card_id="group:term", kind="software", name="Лицензия", vendor_ids=[],
                class_id="software", roles=[], shelf_ids=[], offer_count=2, available_count=2,
                facet_values=[], catalog_version="test")
    candidate = Candidate(card_id=card.card_id, channels=["exact"], channel_ranks=[],
                          matched_role_ids=[], evidence=[], relevance="confirmed")
    far = Offer(offer_id="1", card_id=card.card_id, name="1 месяц", price_state="known",
                price=Money(amount_minor=100, currency="RUB"), available=True, attrs=[short],
                sale_unit="license", raw_params=[], source_refs=[], catalog_version="test")
    near = far.model_copy(update={
        "offer_id": "12", "name": "12 месяцев", "attrs": [year],
        "price": Money(amount_minor=900, currency="RUB"),
    })
    state = QueryState(goal="на 24 месяца", mode="direct", filters=[
        _number("term", "term_months", "eq", 24, "24 месяца"),
    ])
    checked = verify_card(candidate, card, [far, near], state, monotonic() + 2)
    assert checked.match_level == "partial"
    assert checked.selected_offer.offer_id == "12"


def test_unavailable_memory_match_stays_partial():
    card, candidate = _hardware("offer:big", [])
    offer = _gpu_offer("80gb", card.card_id, 80, 1000, available=False)
    state = QueryState(goal="от 48 ГБ", mode="expert", filters=[
        _number("vram", "vram_gb", "gte", 48, "от 48 ГБ"),
        _number("stock", "available", "eq", True, "в наличии"),
    ])
    checked = verify_card(candidate, card, [offer], state, monotonic() + 2)
    assert checked.match_level == "partial"
    assert [condition.status for condition in checked.selected_offer.conditions] == ["match", "mismatch"]


def test_required_component_keeps_both_numeric_examples():
    from smart_site_search.search.engine import PageCard, compose_expert_page

    def card(card_id, component, reserve, available, meets, rank):
        return PageCard(card_id, component, reserve, available, meets, rank)

    cards = [
        card("gpu-12", "gpu", True, True, False, (1, 12)),
        card("gpu-80", "gpu", True, False, True, (1, 0)),
        *[card(f"other-{index}", f"component-{index}", False, True, False, (0, index)) for index in range(4)],
    ]
    page = [item.card_id for item in compose_expert_page(cards, 4)]
    assert {"gpu-12", "gpu-80"} <= set(page)
    assert len(page) == 4

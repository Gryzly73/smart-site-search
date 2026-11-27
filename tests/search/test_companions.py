import pytest
from pydantic import ValidationError

from smart_site_search.contracts.api import CompanionCard
from smart_site_search.contracts.catalog import Card
from smart_site_search.search.companions import (
    ANTIVIRUS_TARGETS,
    DESKTOP_BACKUP_TARGETS,
    OFFICE_365_SECURITY_TARGETS,
    choose_companions,
    rule_for,
    vendor_section_url,
)
from smart_site_search.service import _companion_url


def make(card_id, *, class_id="unclassified", offers=1, available=0, kind="software"):
    return Card(
        card_id=card_id, kind=kind, name=card_id, vendor_ids=[], class_id=class_id,
        roles=[], shelf_ids=[], offer_count=offers, available_count=available,
        facet_values=[], catalog_version="test",
    )


def test_companion_card_has_no_offer_fields():
    card = CompanionCard(
        card_id="group:4069", name="Dr.Web", vendor_names=["Доктор Веб"], kind="software",
        class_id="endpoint_protection", role_label="Антивирус", anchor_card_id="offer:542026",
    )
    assert "selected_offer_id" not in card.model_dump()
    with pytest.raises(ValidationError):
        CompanionCard(
            card_id="group:4069", name="Dr.Web", vendor_names=[], kind="software",
            class_id="endpoint_protection", role_label="Антивирус", anchor_card_id="offer:542026",
            selected_offer_id="1", price_label="1 ₽",
        )


def test_laptop_keeps_one_available_card_per_role():
    anchor = make("offer:125344", class_id="laptop", kind="hardware")
    rule = rule_for(anchor)
    cards = []
    for role in rule.roles:
        cards.append(make(role.card_ids[0], offers=1, available=1))
        cards.append(make(role.card_ids[1], offers=80, available=1))
        cards.append(make(role.card_ids[-1], offers=500, available=0))
    chosen = choose_companions(anchor, cards, set())
    assert [label for label, _ in chosen] == ["ОС рабочего места", "Офисный пакет", "Антивирус"]
    assert [card.card_id for _, card in chosen] == [role.card_ids[1] for role in rule.roles]


def test_unavailable_role_still_returns_the_largest_card():
    anchor = make("group:121231")
    assert rule_for(anchor) is not None
    small = make(DESKTOP_BACKUP_TARGETS[0], offers=4, available=0)
    large = make(DESKTOP_BACKUP_TARGETS[1], offers=40, available=0)
    chosen = choose_companions(anchor, [small, large, make(ANTIVIRUS_TARGETS[0], offers=3, available=0)], set())
    assert [card.card_id for _, card in chosen] == [ANTIVIRUS_TARGETS[0], DESKTOP_BACKUP_TARGETS[1]]


def test_missing_or_already_shown_cards_leave_no_companions():
    anchor = make("group:306539")
    assert choose_companions(anchor, [], set()) == []
    best = make(ANTIVIRUS_TARGETS[0], offers=100, available=5)
    second = make(ANTIVIRUS_TARGETS[1], offers=10, available=1)
    assert choose_companions(anchor, [best], {best.card_id}) == []
    chosen = choose_companions(anchor, [best, second], {best.card_id})
    assert [card.card_id for _, card in chosen] == [ANTIVIRUS_TARGETS[1]]


def test_accessories_and_licenses_are_not_anchors():
    assert rule_for(make("offer:487319", kind="hardware")) is None
    assert rule_for(make("offer:531626", kind="hardware")) is None
    assert rule_for(make("group:161851")) is None
    assert rule_for(make("group:162314", class_id="remote_access")) is None
    assert rule_for(make("offer:1", class_id="unclassified", kind="hardware")) is None


def test_role_counts_follow_the_document():
    assert [role.label for role in rule_for(make("offer:542026", class_id="server_hardware", kind="hardware")).roles] == [
        "Серверная операционная система", "Виртуализация", "Резервное копирование",
    ]
    assert len(rule_for(make("group:12822")).roles) == 2
    assert [role.label for role in rule_for(make("group:12855")).roles] == ["Антивирус"]
    assert [role.card_ids for role in rule_for(make("group:165034")).roles][1] == OFFICE_365_SECURITY_TARGETS
    assert [role.label for role in rule_for(make("group:162037")).roles] == ["Антивирус"]
    assert rule_for(make("offer:449258", class_id="nas", kind="hardware")).roles[0].label == "Резервное копирование"
    assert rule_for(make("group:22268")).roles[0].label == "Офисный пакет"


def test_vendor_section_keeps_only_a_two_part_store_path():
    assert vendor_section_url(
        "https://store.softline.ru/drweb/lhm-bk-12m-2-a3-332614/"
    ) == "https://store.softline.ru/drweb/"
    assert vendor_section_url("https://store.softline.ru/drweb/sku") == "https://store.softline.ru/drweb/"
    assert vendor_section_url("https://store.softline.ru/a/b/c/") is None
    assert vendor_section_url("https://store.softline.ru/drweb/") is None
    assert vendor_section_url("http://user:pass@store.softline.ru/drweb/sku/") is None
    assert vendor_section_url("notaurl") is None
    assert vendor_section_url(None) is None
    assert vendor_section_url("") is None


class _OfferUrl:
    def __init__(self, url):
        self.url = url

    def offer_url(self, card_id):
        return self.url


def test_companion_url_keeps_an_existing_card_address():
    card = make("offer:1", kind="hardware").model_copy(update={
        "card_url": "https://store.softline.ru/hardware/item/",
    })
    assert _companion_url(
        _OfferUrl("https://store.softline.ru/drweb/sku/"), card,
    ) == "https://store.softline.ru/hardware/item/"


def test_companion_url_uses_the_vendor_section_when_the_card_has_no_address():
    assert _companion_url(
        _OfferUrl("https://store.softline.ru/drweb/lhm-bk-12m-2-a3-332614/"), make("group:4069"),
    ) == "https://store.softline.ru/drweb/"
    assert _companion_url(_OfferUrl(None), make("group:4069")) is None
    assert _companion_url(_OfferUrl("https://store.softline.ru/drweb/"), make("group:4069")) is None

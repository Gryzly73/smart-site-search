"""Дополнительные карточки по docs/proposed_relations.md.

Списки собраны по снимку catalog-f62e28077667b7d564b7. Запрос пользователя сюда не подставляется:
сумка, стойка, CAL и чужой продукт со словом Windows в названии не становятся якорем.
"""
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from ..contracts.catalog import Card

SERVER_IDS = frozenset({
    "offer:542026", "offer:542027", "offer:542028", "offer:542029", "offer:542030", "offer:542031", "offer:542032",
    "offer:467607", "offer:467608", "offer:467609", "offer:528224",
    "offer:438239", "offer:438240", "offer:438241", "offer:528223", "offer:467610", "offer:528225",
    "offer:482071", "offer:482054", "offer:531625",
    "offer:539260", "offer:483475", "offer:539251",
})
DESKTOP_OS_IDS = frozenset({
    "group:286676", "group:310176", "group:295882", "group:3564", "group:65851",
    "group:121231", "group:407730", "group:165644", "group:409900", "group:13078", "group:13122", "group:388019",
})
SERVER_OS_IDS = frozenset({
    "group:161826", "group:294539", "group:281974", "group:161664", "group:2749",
    "group:408335", "group:5235",
})
VSPHERE_IDS = frozenset({
    "group:12822", "group:269624", "group:269632", "group:12907", "group:266520",
    "group:269641", "group:269600", "group:269608", "group:282088", "group:12876",
    "group:12858", "group:269591", "group:269582",
})
WORKSTATION_VIRT_IDS = frozenset({"group:12985", "group:12812", "group:12855"})
MICROSOFT_365_IDS = frozenset({
    "group:165034", "group:283494", "group:65743", "group:169704", "group:65824", "group:255172",
})
OFFICE_IDS = frozenset({
    "group:222227", "group:162199", "group:222293", "group:162160", "group:344604", "group:162037",
    "group:162056", "group:162222", "group:162743", "group:161807", "group:162269", "group:161788",
    "group:162110", "group:162090", "group:162725", "group:24300", "group:284105", "group:414853",
    "group:3170", "group:330399", "group:391335", "group:391338",
})
REMOTE_IDS = frozenset({
    "group:306539", "group:61968", "group:5508", "group:33846", "group:120894",
    "group:48080", "group:225238", "group:225470",
})
CRYPTOPRO_CSP_ID = "group:22268"

SERVER_OS_TARGETS = ("group:408335", "group:5235", "group:161826", "group:294539", "group:281974", "group:161664")
HYPERVISOR_TARGETS = ("group:409875", "group:12822")
BACKUP_TARGETS = ("group:173615", "group:344905", "group:123712", "group:64508")
DESKTOP_OS_TARGETS = (
    "group:121231", "group:407730", "group:165644", "group:409900", "group:286676", "group:310176",
    "group:3564", "group:65851", "group:295882", "group:13078", "group:13122", "group:388019",
)
OFFICE_TARGETS = (
    "group:391338", "group:391335", "group:165034", "group:283494", "group:65743", "group:222227",
    "group:344604", "group:162037", "group:161807", "group:161788",
)
ANTIVIRUS_TARGETS = (
    "group:4069", "group:193420", "group:325675", "group:329406", "group:4052", "group:3761",
    "group:3953", "group:3935", "group:3971", "group:437421",
)
DESKTOP_BACKUP_TARGETS = ("group:444870", "group:173614")
DIRECTORY_TARGETS = ("group:392740", "group:65831")
VIRTUAL_BACKUP_TARGETS = ("group:173615", "group:123712", "group:344905")
VIRTUAL_SECURITY_TARGETS = ("group:3872",)
OFFICE_365_SECURITY_TARGETS = ("group:124091",)


@dataclass(frozen=True)
class CompanionRole:
    label: str
    card_ids: tuple[str, ...]


@dataclass(frozen=True)
class CompanionRule:
    roles: tuple[CompanionRole, ...]


def _rule(*roles: CompanionRole) -> CompanionRule:
    return CompanionRule(roles)


_SERVER = _rule(
    CompanionRole("Серверная операционная система", SERVER_OS_TARGETS),
    CompanionRole("Виртуализация", HYPERVISOR_TARGETS),
    CompanionRole("Резервное копирование", BACKUP_TARGETS),
)
_LAPTOP = _rule(
    CompanionRole("ОС рабочего места", DESKTOP_OS_TARGETS),
    CompanionRole("Офисный пакет", OFFICE_TARGETS),
    CompanionRole("Антивирус", ANTIVIRUS_TARGETS),
)
_DESKTOP = _rule(
    CompanionRole("Антивирус", ANTIVIRUS_TARGETS),
    CompanionRole("Резервное копирование", DESKTOP_BACKUP_TARGETS),
)
_SERVER_SOFTWARE = _rule(
    CompanionRole("Резервное копирование", BACKUP_TARGETS),
    CompanionRole("Виртуализация", HYPERVISOR_TARGETS),
    CompanionRole("Служба каталогов", DIRECTORY_TARGETS),
)
_VSPHERE = _rule(
    CompanionRole("Копии виртуальных машин", VIRTUAL_BACKUP_TARGETS),
    CompanionRole("Защита виртуальных сред", VIRTUAL_SECURITY_TARGETS),
)
_ANTIVIRUS = _rule(CompanionRole("Антивирус", ANTIVIRUS_TARGETS))
_MICROSOFT_365 = _rule(
    CompanionRole("Антивирус", ANTIVIRUS_TARGETS),
    CompanionRole("Защита Microsoft 365", OFFICE_365_SECURITY_TARGETS),
)
_OFFICE = _rule(CompanionRole("Антивирус", ANTIVIRUS_TARGETS))
_REMOTE = _rule(CompanionRole("Антивирус", ANTIVIRUS_TARGETS))
_NAS = _rule(CompanionRole("Резервное копирование", BACKUP_TARGETS))
_CRYPTOPRO = _rule(CompanionRole("Офисный пакет", OFFICE_TARGETS))


def rule_for(card: Card | None) -> CompanionRule | None:
    if card is None:
        return None
    card_id = card.card_id
    if card_id in MICROSOFT_365_IDS:
        return _MICROSOFT_365
    if card_id in OFFICE_IDS:
        return _OFFICE
    if card_id in WORKSTATION_VIRT_IDS:
        return _ANTIVIRUS
    if card_id in VSPHERE_IDS:
        return _VSPHERE
    if card_id in SERVER_OS_IDS:
        return _SERVER_SOFTWARE
    if card_id in DESKTOP_OS_IDS:
        return _DESKTOP
    if card_id in REMOTE_IDS:
        return _REMOTE
    if card_id == CRYPTOPRO_CSP_ID:
        return _CRYPTOPRO
    if card.class_id == "nas":
        return _NAS
    if card.class_id == "laptop":
        return _LAPTOP
    if card.class_id == "server_hardware" or card_id in SERVER_IDS:
        return _SERVER
    return None


def _pick(cards: list[Card]) -> Card | None:
    if not cards:
        return None
    available = [card for card in cards if card.available_count > 0]
    pool = available or cards
    return max(pool, key=lambda card: (card.offer_count, card.available_count, card.card_id))


def choose_companions(anchor: Card | None, cards: list[Card], excluded: set[str]) -> list[tuple[str, Card]]:
    rule = rule_for(anchor)
    if rule is None or anchor is None:
        return []
    by_id = {card.card_id: card for card in cards}
    blocked = set(excluded)
    blocked.add(anchor.card_id)
    chosen = []
    for role in rule.roles:
        pick = _pick([by_id[card_id] for card_id in role.card_ids if card_id in by_id and card_id not in blocked])
        if pick is None:
            continue
        blocked.add(pick.card_id)
        chosen.append((role.label, pick))
    return chosen


def vendor_section_url(offer_url: str | None) -> str | None:
    """Раздел вендора из адреса лицензии: /drweb/sku/ становится /drweb/."""
    if not offer_url or not offer_url.strip():
        return None
    parts = urlsplit(offer_url.strip())
    segments = [piece for piece in parts.path.split("/") if piece]
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        return None
    if len(segments) != 2:
        return None
    return urlunsplit((parts.scheme, parts.netloc, f"/{segments[0]}/", "", ""))


def select_companions(catalog, anchor: Card | None, excluded: set[str]) -> list[tuple[str, Card]]:
    rule = rule_for(anchor)
    if rule is None or anchor is None:
        return []
    wanted = tuple(dict.fromkeys(card_id for role in rule.roles for card_id in role.card_ids))
    return choose_companions(anchor, catalog.get_cards(wanted), excluded)

"""Deterministic, source-backed offer attribute extraction."""
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html import unescape

from ..contracts.catalog import Attribute, Scalar, SourceRef

NORMALIZER_VERSION = "offer-rules-v1"
NUMERIC_FILTER_KEYS = frozenset({
    "vram_gb", "ram_gib", "storage_gb", "cores", "users", "devices", "nodes",
    "term_months", "price_minor",
})
UNKNOWN = {"", "-", "—", "другое или отсутствует", "не определено", "не указано", "n/a"}
MULTIVALUED = {"certificate", "platform", "architecture", "registry_number"}
TERM_PATTERN = re.compile(r"(?<!\w)(\d+)\s*(мес(?:яц\w*)?\.?|год(?:а|ов)?|лет)(?!\w)")
ORDER_PATTERN = re.compile(r"кол[\s.-]*во\s+(?:от\s+(\d+)\s+до\s+(\d+)|от\s+(\d+)|до\s+(\d+))\s*шт\.?")
QUANTITY_PATTERN = re.compile(
    r"(?<![\w.-])(\d+)(?:\s*[-–—]\s*(\d+))?\s+"
    r"(пользовател\w*|устройств\w*|рабочи[ех]\s+станци\w*|уз(?:ел|л\w*)|я(?:дро|др\w*|дер))\b"
)
NUMBER_PATTERN = re.compile(r"(\d+)\s*(gb|гб|gib|гиб|tb|тб|tib|тиб)?")
MEMORY_UNIT = {"gb": "GB", "гб": "GB", "gib": "GiB", "гиб": "GiB", "tb": "GB", "тб": "GB", "tib": "GiB", "тиб": "GiB"}
SCALED_UNITS = {"tb": 1000, "тб": 1000, "tib": 1024, "тиб": 1024}

ENUMS = {
    "delivery_type": {
        "electronic": r"электрон\w*|esd|download",
        "media": r"физическ\w*|коробочн\w*|dvd|usb",
        "subscription": r"подписка",
    },
    "customer_type": {
        "legal_entity": r"юрлицо|юрлиц\w*|юридическ\w* лиц\w*",
        "individual": r"физлицо|физлиц\w*|физическ\w* лиц\w*",
    },
    "organization_type": {
        "commercial": r"коммерческ\w*",
        "academic": r"академическ\w*|образовательн\w*|учебн\w*",
        "government": r"государственн\w*|бюджетн\w*",
        "nonprofit": r"некоммерческ\w*",
    },
    "license_type": {
        "full": r"полн\w* верси\w*|новая лицензия|full",
        "renewal": r"продлен\w*|renewal",
        "upgrade": r"переход на новую версию|обновлен\w*|upgrade",
        "subscription": r"подписк\w*|subscription",
        "support": r"техническ\w* поддержк\w*|техподдержк\w*|support",
        "trial": r"пробн\w* верси\w*|trial",
        "oem": r"oem",
    },
    "architecture": {
        "x86_64": r"[xх]86[\s_-]64|amd64|x64",
        "x86": r"[xх]86(?![\s_-]64)|i[3-6]86",
        "arm64": r"arm64|aarch64|armv8",
        "arm": r"arm(?:v[5-7])?",
        "e2k": r"e2k|эльбрус",
        "mips": r"mips",
        "riscv64": r"risc-?v(?:64)?",
    },
    "platform": {
        "virtualization": r"платформ\w* виртуализац\w*",
        "virtual_machine": r"виртуальн\w* машин\w*|virtual machines?",
        "physical": r"физическ\w* сервер\w*|physical servers?",
        "vmware": r"vmware|vsphere|esxi",
        "hyper_v": r"hyper[\s-]?v",
        "windows": r"windows",
        "linux": r"linux|линукс",
        "macos": r"mac ?os",
        "android": r"android",
        "ios": r"ios",
    },
    "certificate": {"fstec": r"фстэк|fstec", "fsb": r"фсб|fsb"},
}
ENUM_PATTERNS = {
    key: {value: re.compile(rf"(?<!\w)(?:{expression})(?!\w)") for value, expression in possibilities.items()}
    for key, possibilities in ENUMS.items()
}
PARAM_KEYS = {
    "способ доставки": ("delivery_type",),
    "тип поставки": ("delivery_type",),
    "пользователь": ("customer_type",),
    "тип организации": ("organization_type",),
    "тип лицензии": ("license_type",),
    "номер записи в реестре отечественного по": ("registry_number",),
    "сертификаты и стандарты": ("certificate",),
    "совместимая архитектура/платформа": ("architecture", "platform"),
    "платформа": ("platform",),
    "назначение": ("platform",),
    "архитектура": ("architecture",),
    "редакция": ("edition",),
    "форм-фактор": ("form_factor",),
    "форм фактор": ("form_factor",),
    "сокет": ("socket",),
    "количество пользователей": ("users",),
    "количество устройств": ("devices",),
    "количество узлов": ("nodes",),
    "количество ядер": ("cores",),
}
UNITS = {"term_months": "month", "users": "user", "devices": "device", "nodes": "node", "cores": "core"}


def normalized(text: str) -> str:
    return unicodedata.normalize("NFKC", unescape(text)).casefold().replace("ё", "е")


def search_text(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", normalized(text)))


def currency_and_price(raw_currency: str, raw_price: str) -> tuple[str | None, int | None, str]:
    currency = {"RUR": "RUB"}.get(raw_currency.strip().upper(), raw_currency.strip().upper())
    valid_currency = currency if re.fullmatch(r"[A-Z]{3}", currency) else None
    try:
        price = Decimal(raw_price.replace(" ", "").replace("\u00a0", "").replace(",", "."))
        if not price.is_finite() or price < 0:
            return valid_currency, None, "unknown"
        if price.is_zero():
            return valid_currency, None, "on_request"
        minor = price * 100
        if not valid_currency or minor != minor.to_integral_value() or minor >= 2**63:
            return valid_currency, None, "unknown"
    except InvalidOperation:
        return valid_currency, None, "unknown"
    return valid_currency, int(minor), "known"


def order_bounds(notes: str) -> tuple[int | None, int | None]:
    match = ORDER_PATTERN.fullmatch(normalized(notes).strip())
    if not match:
        return None, None
    low = int(match[1] or match[3]) if match[1] or match[3] else None
    high = int(match[2] or match[4]) if match[2] or match[4] else None
    if low is not None and high is not None and low > high:
        return None, None
    return low, high


@dataclass(frozen=True)
class Param:
    name: str
    value: str
    index: int
    unit: str | None


@dataclass(frozen=True)
class Fact:
    value: Scalar | tuple[int, int] | None
    field: str
    raw_index: int | None
    unit: str | None = None


def _enum_values(key: str, text: str) -> list[str]:
    return [value for value, pattern in ENUM_PATTERNS[key].items() if pattern.search(text)]


def _term_values(text: str):
    for match in TERM_PATTERN.finditer(text):
        months = int(match[1]) * (1 if match[2].startswith("мес") else 12)
        if months > 0:
            yield "term_months", months
            yield "perpetual", False
    if re.search(r"\b(?:бессрочн\w*|бессрочно|perpetual)\b", text):
        yield "perpetual", True


def _memory_value(key: str, text: str, unit: str | None):
    match = NUMBER_PATTERN.fullmatch(text.strip())
    if not match:
        return None
    if unit and match[2] and MEMORY_UNIT.get(normalized(unit)) != MEMORY_UNIT.get(match[2]):
        return None
    label = match[2] or normalized(unit or "")
    recognized = MEMORY_UNIT.get(label)
    if recognized is None:
        return None
    return f"{key}_{recognized.lower()}", int(match[1]) * SCALED_UNITS.get(label, 1), recognized


def _parameter_facts(param: Param, kind: str, is_gpu: bool):
    name = normalized(param.name).strip()
    text = normalized(param.value).strip()
    keys = PARAM_KEYS.get(name, ())
    if name == "срок действия" and kind == "software":
        yield from ((key, value, UNITS.get(key)) for key, value in _term_values(text))
        if not list(_term_values(text)):
            yield "term_months", None, "month"
            yield "perpetual", None, None
        return
    memory_kind = None
    if kind == "hardware":
        if is_gpu and name in {"объем памяти", "объем видеопамяти", "объем графической памяти"}:
            memory_kind = "vram"
        elif name in {"объем оперативной памяти", "объем озу", "объем установленной оперативной памяти"}:
            memory_kind = "ram"
        elif name in {"объем накопителя", "емкость накопителя", "объем hdd", "объем ssd"}:
            memory_kind = "storage"
    if memory_kind:
        memory = _memory_value(memory_kind, text, param.unit)
        if memory:
            yield memory
    for key in keys:
        if text in UNKNOWN:
            yield key, None, UNITS.get(key)
        elif key in ENUM_PATTERNS:
            values = _enum_values(key, text)
            if not values and key == "certificate":
                values = [param.value.strip()]
            for value in values or [None]:
                yield key, value, None
        elif key in {"users", "devices", "nodes", "cores"}:
            match = re.fullmatch(r"(\d+)(?:\s*[-–—]\s*(\d+))?", text)
            value = None
            if match and match[2] and int(match[1]) <= int(match[2]):
                value = (int(match[1]), int(match[2]))
            elif match and not match[2]:
                value = int(match[1])
            yield key, value, UNITS[key]
        else:
            yield key, text, None


def _name_facts(name: str, kind: str, is_gpu: bool):
    name = normalized(name)
    if kind == "software":
        yield from ((key, value, UNITS.get(key)) for key, value in _term_values(name))
        for key in ("architecture", "platform", "customer_type", "organization_type", "certificate"):
            if key == "certificate" and not re.search(r"\bсертификат\w*|\bсертифицирова", name):
                continue
            yield from ((key, value, None) for value in _enum_values(key, name))
        for match in QUANTITY_PATTERN.finditer(name):
            low, high, metric = match.groups()
            key = next(
                key for pre, key in (("п", "users"), ("устр", "devices"), ("раб", "devices"), ("уз", "nodes"), ("я", "cores"))
                if metric.startswith(pre)
            )
            if not high:
                yield key, int(low), UNITS[key]
            elif int(low) <= int(high):
                yield key, (int(low), int(high)), UNITS[key]
    elif is_gpu:
        for match in re.finditer(r"(?<![\w.])(\d+)\s*(gb|гб|γб|gib|гиб)\b", name):
            value = _memory_value("vram", match[1] + match[2].replace("γ", "г"), None)
            if value:
                yield value


def normalize_attributes(
    params: list[Param], name: str, kind: str, is_gpu: bool,
    *, available: bool | None, sale_notes: str, offer_id: str, version: str,
) -> list[Attribute]:
    facts = defaultdict(list)
    for param in params:
        for key, value, unit in _parameter_facts(param, kind, is_gpu):
            facts[key].append(Fact(value, f"param:{param.name}", param.index, unit))
    for key, value, unit in _name_facts(name, kind, is_gpu):
        facts[key].append(Fact(value, "name", None, unit))
    if kind == "software":
        for key in ("customer_type", "organization_type", "certificate", "registry_number", "term_months", "perpetual"):
            facts.setdefault(key, [])
    if facts.get("term_months") and not any(f.value is not None for f in facts["term_months"]):
        perpetual_facts = [fact for fact in facts.get("perpetual", ()) if fact.value is True]
        facts["term_months"].extend(Fact(None, item.field, item.raw_index, "month") for item in perpetual_facts)
    facts["available"].append(Fact(available, "available", None))
    return [
        _attribute(key, values, offer_id, version)
        for key, values in sorted(facts.items())
    ]


def _attribute(key: str, facts: list[Fact], offer_id: str, version: str) -> Attribute:
    known_facts = [fact for fact in facts if fact.value is not None]
    used_facts = known_facts or facts
    values = list(dict.fromkeys(fact.value for fact in known_facts))
    unit_values = {fact.unit for fact in used_facts}
    knowledge = "known" if values else "unknown"
    if (key not in MULTIVALUED and len(values) > 1) or len(unit_values) > 1:
        knowledge = "conflicting"
    ranges = [value for value in values if isinstance(value, tuple)]
    scalars = [value for value in values if not isinstance(value, tuple)]
    low, high = ranges[0] if len(ranges) == 1 and len(values) == 1 else (None, None)
    return Attribute(
        key=key, knowledge=knowledge, values=scalars,
        unit=next(iter(unit_values)) if len(unit_values) == 1 else UNITS.get(key),
        min_value=low, max_value=high,
        source_refs=[
            SourceRef(source="feed", catalog_version=version, entity_id=offer_id, field=field, raw_index=index)
            for field, index in dict.fromkeys((fact.field, fact.raw_index) for fact in used_facts)
        ],
    )

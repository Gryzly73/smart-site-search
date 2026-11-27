"""Small Russian grammar used for proven local constraints and provider fallback."""

import re
import unicodedata
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from smart_site_search.contracts.intent import Entity, Filter, FilterChange, Interpretation, Reference
from smart_site_search.session import resolve_reference


def norm(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().replace("ё", "е").split())


def lookup_entities(taxonomy):
    return [e for e in taxonomy.get("lookup", []) if isinstance(e, dict)
            and e.get("type") in ("sku", "product", "vendor") and e.get("text") and e.get("resolved_id")
            and (e["type"] == "vendor" or str(e["resolved_id"]).startswith(("group:", "offer:")))]


SESSION_SCOPE = re.compile(r"(?:во всех (?:следующих|будущих|запросах)|для всех (?:следующих )?запросов|на всю сессию)")
NEW_TOPIC = re.compile(r"\b(?:новая тема|теперь (?:нужен|ищу))")
NUMBER = r"(?:\d+|один|одну|два|две|три)"
NUMBERS = {"один": 1, "одну": 1, "два": 2, "две": 2, "три": 3}
QUANTITY = r"(?<![\w.,])(?:(?P<ceiling>до)\s+|на\s+)?(?P<n>\d+(?: \d{3})*)"
GPU_TERMS = r"(?:gpu|видеокарт[а-я]*|видеопамят[а-я]*|ускорител[а-я]*|карт[а-я]* для рендеринга)"
PRICE_LIMIT = re.compile(
    r"(?<!\w)(?P<marker>(?P<total_prefix>(?:общий|суммарный)\s+)?бюджет(?:ом)?\s*[:=]?\s*(?:до\s*)?|"
    r"(?:по цене|цена|стоимость)\s*[:=]?\s*(?:до\s*)?|не дороже\s+|"
    r"(?:строго\s+)?до\s+|не (?:более|больше)\s+|максимум\s+)"
    r"(?P<symbol>[₽$€])?\s*"
    r"(?P<amount>(?:\d{1,3}(?: \d{3})+|\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,]\d{1,2})?)"
    r"(?!\d|[.,]\d)"
    r"(?:(?P<scale>[кk]|\s*(?:тыс(?:яч[аиу]?)?|млн|миллион(?:а|ов)?|млрд))\b\.?)?"
    r"(?:\s*(?P<currency>[₽$€]|(?:руб(?:ль|ля|лей)?|rub|rur|usd|eur|cny|kzt|byn|доллар(?:а|ов)?|евро)\b)\.?)?"
    r"(?:\s+(?P<basis>за (?:все|весь комплект|всю партию|одну лицензию|лицензию|штуку))\b)?"
)
PRICE_CURRENCIES = {"₽": "RUB", "rur": "RUB", "$": "USD", "€": "EUR", "евро": "EUR"}
PURCHASE_CONTEXT = re.compile(r"\b(?:подбер\w*|подобр\w*|най[дт]\w*|ищу|покаж\w*|куп\w*)\b")
NON_PRICE_PREFIX = re.compile(
    r"\b(?:верси[а-я]*|сборк[а-я]*|релиз[а-я]*|обновлен[а-я]*|дат[аеуы]|срок[а-я]*|"
    r"гаранти[а-я]*|количество|число|емкост[а-я]*|мощност[а-я]*|задержк[а-я]*|скорост[а-я]*)\s*$"
)
POSSIBLE_DATE = re.compile(r"(?:19|20)\d{2}|(?:0?[1-9]|[12]\d|3[01])\.(?:0?[1-9]|1[0-2])")


def price_currency(value):
    if value.startswith("руб"):
        return "RUB"
    if value.startswith("доллар"):
        return "USD"
    return PRICE_CURRENCIES.get(value, value.upper())


def price_limits(query, *, catalog_context, previous_price):
    for match in PRICE_LIMIT.finditer(query):
        marker, total_prefix, symbol, amount, scale, currency, basis = match.groups()
        scale = (scale or "").strip()
        if symbol and currency and price_currency(symbol) != price_currency(currency):
            continue
        if not (symbol or currency):
            tail = query[match.end():]
            if not basis and tail and not re.match(r"(?:[,;!?]|\.(?!\w)|\s+(?:и|с|для|на)\s+\S)", tail):
                continue
            prefix = query[:match.start()]
            if NON_PRICE_PREFIX.search(prefix):
                continue
            explicit = "бюджет" in marker or marker.startswith(("по цене", "цена", "стоимость", "не дороже")) or basis
            if not (explicit or scale or catalog_context or PURCHASE_CONTEXT.search(prefix)):
                continue
            if not (explicit or scale or previous_price) and POSSIBLE_DATE.fullmatch(amount):
                continue
        number = amount.replace(" ", "")
        number = re.sub(r"[.,](?=\d{3}(?!\d))", "", number)
        if scale.startswith("тыс") or scale in ("к", "k"):
            multiplier = 1_000
        elif scale == "млрд":
            multiplier = 1_000_000_000
        else:
            multiplier = 1_000_000 if scale else 1
        minor = Decimal(number.replace(",", ".")) * multiplier * 100
        if 0 < minor < 2**63 and minor == minor.to_integral_value():
            if total_prefix or basis in ("за все", "за весь комплект", "за всю партию"):
                key = "total_price_minor"
            elif previous_price and not basis and marker.strip() in (
                "до", "строго до", "максимум", "не дороже", "не более", "не больше",
            ):
                key = previous_price.key
            else:
                key = "price_minor"
            default_currency = (previous_price.currency if previous_price else None) or "RUB"
            yield match, key, int(minor), price_currency(currency or symbol) if currency or symbol else default_currency


def unsupported_measurements(query):
    return [match[0] for match in re.finditer(
        r"\b(?:выход[а-я]*|напряжени[а-я]*)[^.!?,;\d]{0,24}\d+(?:[.,]\d+)?\s*(?:в|вольт[а-я]*)\b",
        norm(query),
    )]


def integer(value):
    return NUMBERS[value] if value in NUMBERS else int(value)


def make_filter(key, op, values, source, scope="topic", strength="require", currency=None, unit=None,
                component_id=None, turn="current"):
    return Filter(filter_id=str(uuid5(NAMESPACE_URL, repr((turn, scope, component_id, key)))), key=key,
                  op=op, values=values, source_text=source, source_turn_id=turn, scope=scope,
                  strength=strength, currency=currency, unit=unit, component_id=component_id)


def local_intent(query, session, taxonomy):
    q = norm(query)
    previous = session.query_state if session else None
    scope = "session" if SESSION_SCOPE.search(q) else "topic"
    fields = taxonomy.get("fields", {})
    changes, unresolved, entities, markers, terms = [], [], [], [], []
    unresolved.extend(unsupported_measurements(q))
    roles, classes = [], []
    question = None
    text = list(q)

    def mark(start, end):
        text[start:end] = " " * (end - start)

    def set_field(key, op, values, source, strength="require", currency=None):
        meta = fields.get(key)
        if meta is None or isinstance(meta, dict) and op not in meta.get("ops", [op]):
            unresolved.append(source)
            return
        unit = meta.get("unit") if isinstance(meta, dict) else None
        f = make_filter(key, op, values, source, scope, strength, currency, unit)
        changes.append(FilterChange(action="set", scope=scope, key=key, filter=f))

    # Operations on saved filters apply even when a provider is unavailable.
    for keys, pat in ((("term_months",), r"срок (?:неважен|не важен|любой)|без ограничени[йя] (?:по )?срок[ау]"),
                      (("price_minor", "total_price_minor"),
                       r"без ограничени[йя] (?:по )?(?:цен[ые]|бюджет[ау])|(?:цена|бюджет) (?:не ?важ[а-я]+|любо[яй])"),
                      (("vendor_id",), r"любой (?:вендор|производитель)|без ограничени[йя] по (?:вендору|производителю)")):
        for m in re.finditer(pat, q):
            changes.extend(FilterChange(action="remove", scope=scope, key=key) for key in keys)
            mark(*m.span())
    for pat, key, expr in (
        (rf"(?:на\s+)?(?:(?P<n>{NUMBER})\s+)?(?:год(?:а|ов)?|лет)\b", "term_months", lambda m: 12 * integer(m["n"] or "1")),
        (rf"(?:на\s+)?(?P<n>{NUMBER})\s+месяц[а-я]*", "term_months", lambda m: integer(m["n"])),
        (rf"{QUANTITY}\s*(?:комп(?:ов|ьютер[а-я]*)?|устройств[а-я]*|пк)\b", "devices", lambda m: int(m["n"].replace(" ", ""))),
        (rf"{QUANTITY}\s*пользовател[а-я]*", "users", lambda m: int(m["n"].replace(" ", ""))),
        (r"(?:для\s+)?(?:юр\.?\s*лиц[а-я]*|юридическ[а-я]* лиц[а-я]*)", "customer_type", lambda m: "legal_entity"),
        (r"коммерческ[а-я]*\s+(?:организаци[а-я]*|компани[а-я]*|юр\.?\s*лиц[а-я]*|юридическ[а-я]* лиц[а-я]*)", "organization_type", lambda m: "commercial"),
        (r"\bэлектронн[а-я]*\s+(?:(?:бессрочн[а-я]*|полн[а-я]*)\s+)?(?:лицензи[а-я]*|поставк[а-я]*)", "delivery_type", lambda m: "electronic"),
        (r"\bбессрочн[а-я]*\b", "perpetual", lambda m: True),
        (r"\b(?:сертификат[а-я]*\s+)?фстэк\b", "certificate", lambda m: "fstec"),
        (r"\b(?:x86(?:[-_]64)?|amd64)\b", "architecture", lambda m: "x86_64"),
        (r"\b(?:для\s+)?платформ[а-я]*\s+виртуализаци[а-я]*\b", "platform", lambda m: "virtualization"),
        (r"(?:в наличии|доступн[а-я]* к заказу)", "available", lambda m: True),
        (r"доступн[а-я]*\s+(?:gpu|видеокарт[а-я]*|ускорител[а-я]*)", "available", lambda m: True),
        (r"(?:именно\s+)?(?:по|софт) для сервера", "kind", lambda m: "software"),
    ):
        for m in re.finditer(pat, q):
            set_field(key, "lte" if m.groupdict().get("ceiling") else "eq", [expr(m)], m[0])
            mark(*m.span())
    for m in re.finditer(rf"\b(?:{GPU_TERMS}.{{0,45}}?|(?=(?:от|минимум|не менее)\s+\d+\s*(?:гб|gb)\s+{GPU_TERMS}))"
                         r"(?:от|минимум(?: с)?|не менее|хотя бы)\s*(\d+)\s*(?:гб|gb)\b", q):
        set_field("vram_gb", "gte", [int(m[1])], m[0])
        mark(*m.span())
        if "gpu" in taxonomy.get("roles", {}):
            roles.append("gpu")
    reference = None
    ordinal = re.search(r"\b(перв[а-я]*|втор[а-я]*|трет[а-я]*|четверт[а-я]*|\d+[-‐]?[йя])(?:\s+вариант[а-я]*)?\b", q)
    excluded_vendor = re.search(
        r"(?:без|кроме) (?:(?:этого|данного) (?:вендора|производителя)|"
        r"(?:вендора|производителя) (?:перв[а-я]*|втор[а-я]*|трет[а-я]*|четверт[а-я]*)\s+карточк[а-я]*)", q)
    selected = taxonomy.get("selected_card_id")
    more = re.search(r"\b(?:еще|другие варианты|следующие варианты)\b", q)
    ref_id = taxonomy.get("reference_request_id")
    if ordinal or selected or excluded_vendor:
        if not ref_id and session and session.recent_results:
            ref_id = session.recent_results[-1].request_id
        pos = None
        if ordinal:
            first = ordinal[1]
            pos = next((i for i, s in enumerate(("перв", "втор", "трет", "четверт"), 1) if first.startswith(s)), None)
            if pos is None:
                pos = int(re.match(r"\d+", first)[0])
            mark(*ordinal.span())
        if excluded_vendor and not selected and not ordinal and previous:
            selected = previous.reference_card_id
            if selected and not taxonomy.get("reference_request_id"):
                ref_id = next((r.request_id for r in reversed(session.recent_results) if selected in r.display_order), ref_id)
        if ref_id and pos != 0:
            reference = Reference(request_id=ref_id, card_id=selected, ordinal=pos)
        resolved = resolve_reference(session, reference) if session else None
        if not resolved:
            question = "Какую карточку из предыдущей выдачи вы имеете в виду?"
        elif excluded_vendor:
            vendors = taxonomy.get("card_vendors", {}).get(resolved, [])
            if vendors:
                set_field("vendor_id", "in", vendors, excluded_vendor[0], "exclude")
            else:
                question = "Какого производителя нужно исключить?"
        if excluded_vendor:
            mark(*excluded_vendor.span())

    if more:
        mark(*more.span())
    add_ons = re.search(r"(?:и\s+)?что\s+к\s+нему\s+(?:еще\s+)?(?:докупить|подобрать)", q)
    if add_ons:
        mark(*add_ons.span())
    lookups = lookup_entities(taxonomy)
    certain = {e["resolved_id"]: e for e in lookups if e.get("exact") is True}
    for candidate in lookups:
        term = norm(candidate["text"])
        m = re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", q)
        if not m and not (len(certain) == 1 and candidate.get("exact") is True):
            continue
        left = q[:m.start()] if m else ""
        usage = "excluded" if re.search(r"(?:без|кроме|исключая)\s+$", left) else (
            "reference" if re.search(r"(?:аналог|альтернатив[ауы]|вместо|заменить|замена)\s+$", left) else "target")
        entity = Entity(text=candidate["text"], type=candidate["type"], usage=usage, resolved_id=candidate["resolved_id"])
        if entity not in entities:
            entities.append(entity)
        if usage == "excluded":
            set_field("vendor_id" if entity.type == "vendor" else "card_id", "in", [entity.resolved_id], term, "exclude")
        if m:
            mark(*m.span())
    for group, output in (("roles", roles), ("classes", classes)):
        for ident, meta in taxonomy.get(group, {}).items():
            if not isinstance(meta, dict) or meta.get("verified") is False:
                continue
            aliases = [meta.get("label", ""), *meta.get("aliases", [])]
            if ident == "endpoint_security":
                aliases += ["антивирь", "антивирус"]
            if ident == "backup_restore":
                aliases += ["бэкап", "резервное копирование"]
            for alias in aliases:
                if alias and (m := re.search(r"(?<!\w)" + re.escape(norm(alias)) + r"(?!\w)", q)):
                    output.append(ident)
                    markers.append(m[0])
                    terms.append(meta.get("label") or alias)
                    mark(*m.span())
                    break
    previous_price = None
    new_topic_requested = bool(NEW_TOPIC.search(q))
    if previous and not (new_topic_requested or entities or roles or classes):
        previous_price = next((f for f in reversed(previous.filters) if f.key in ("price_minor", "total_price_minor")), None)
    for m, key, amount, currency in price_limits(q, catalog_context=bool(entities or roles or classes or previous),
                                                previous_price=previous_price):
        if not "".join(text[m.start():m.end()]).strip():
            continue
        set_field(key, "lte", [amount], m[0], currency=currency)
        mark(*m.span())
    deployment = re.search(r"\b(?:развернуть|развертывани[а-я]*|внедрить|развернем)\b", q)
    assembly = re.search(r"\b(?:инфраструктур[а-я]*|комплект для|собрать решение)\b", q)
    local_model = re.search(r"\b(?:llm|языков[а-я]* модел[а-я]*)\b", q) and re.search(
        r"\b(?:локальн[а-я]*|внутри|в офисе|своими силами)\b", q)
    expert = bool(assembly or local_model and (deployment or re.search(GPU_TERMS, q)))
    if q in ("сзи", "сервер"):
        question = ("Какую задачу защиты нужно решить?" if q == "сзи" else
                    "Вы ищете оборудование, серверную операционную систему или другое ПО для сервера?")
    software_server = bool(re.search(r"\b(?:по|софт) для сервера\b", q)
                           and "server_os" in taxonomy.get("roles", {}))
    if software_server:
        roles = [role for role in roles if role != "server_hardware"]
        roles.extend(role for role in ("server_os", "hypervisor") if role in taxonomy["roles"] and role not in roles)
        classes = [value for value in classes
                   if taxonomy.get("classes", {}).get(value, {}).get("kind") != "hardware"]
        terms.append("серверное ПО")
    if software_server:
        op = "new_topic"
    elif more:
        op = "more" if previous else "reference"
        if not previous:
            question = "Для какой задачи нужны дополнительные варианты?"
    elif ordinal or selected or excluded_vendor:
        op = "reference"
    elif previous and (changes or re.match(r"(именно|только|дешевле|подороже|без|кроме)\b", q)) and not (
        new_topic_requested or entities and any(e.usage == "target" for e in entities)
        or (roles or classes) and not set([*roles, *classes]).intersection([*previous.role_ids, *previous.class_ids])):
        op = "refine"
    else:
        op = "new_topic"
    exact = len(certain) == 1 and op == "new_topic" and not changes and not expert and not question
    mode = ("expert" if expert else previous.mode if previous and op != "new_topic" else
            "direct" if entities and all(e.usage == "target" for e in entities) or exact else "functional")
    leftover = "".join(text)
    leftover = SESSION_SCOPE.sub(" ", leftover)
    if add_ons and any(e.type in ("product", "sku") for e in entities):
        leftover = re.sub(r"\bстойков[а-я]*\b", " ", leftover)
    leftover = re.sub(r"\b(?:мне|пожалуйста|нуж(?:ен|на|ны|но)|подбери(?:те)?|найди(?:те)?|ищу|покажи(?:те)?|"
                      r"хочу|теперь|вариант[а-я]*|только|и|а|для|на|без|кроме|еще)\b", " ", leftover)
    leftover = " ".join(re.findall(r"[\w-]+", leftover))
    if exact:
        entities = [e for e in entities if e.resolved_id in certain]
    elif leftover and mode != "expert" and not (
        op == "refine" and re.fullmatch(
            r"(?:(?:подними|повышай|увеличь|снизь|опусти)(?: бюджет)?|обязательно|тогда)", leftover
        )
    ):
        unresolved.append(leftover)
    if mode == "expert" and not question:
        question = "Какие компоненты и условия закупки обязательны?"
    if mode == "functional" and not roles and not classes and op == "new_topic" and not question:
        question = "Уточните задачу или тип продукта, который вы ищете."
    goal = previous.goal if previous and op != "new_topic" else query
    verified_target = any(e.type in ("product", "sku") and e.usage == "target" for e in entities)
    local_complete = not unresolved and not question and (
        software_server or op != "new_topic" or mode == "direct" and verified_target
        and (add_ons is not None
             or any(change.key in ("architecture", "price_minor", "total_price_minor") for change in changes))
    )
    return Interpretation(mode=mode, operation=op, goal=goal, entities=entities, class_ids=classes,
                          role_ids=list(dict.fromkeys(roles)),
                          search_terms=list(dict.fromkeys([*terms, query]))[:12] if op == "new_topic" or terms else [],
                          filter_changes=changes[:20], unresolved_constraints=list(dict.fromkeys(unresolved)),
                          reference=reference, ambiguity="needs_clarification" if question else "none",
                          clarification_question=question,
                          interpretation_source="rules" if exact or local_complete else "degraded_rules")

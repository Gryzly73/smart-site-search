import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from time import monotonic
from uuid import uuid4

from .contracts.api import (
    BudgetAlternative, CompanionCard, Counts, ResultItem, SearchRequest, SearchResponse, Suggestion,
)
from .contracts.events import Provenance
from .contracts.intent import Entity, RecentResult
from .interpretation import Interpreter, Pricing
from .interpretation.rules import norm, unsupported_measurements
from .search import collect_matches
from .search.companions import select_companions, vendor_section_url
from .search.constraints import apply_explicit_constraints
from .search.verify import budget_fit, is_budget_ceiling, misses_stated_requirement
from .session import reduce_context
from .settings import Settings
from .telemetry import ApiError, RuntimeStore


def catalog_readiness(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        from .catalog import CatalogRepository

        return CatalogRepository(path).catalog_version
    except (ImportError, sqlite3.Error, OSError, ValueError):
        return None


def run_search(req: SearchRequest, settings: Settings, store: RuntimeStore) -> SearchResponse:
    if not catalog_readiness(settings.catalog_path):
        raise ApiError(503, "catalog_unavailable", "Снимок каталога ещё подключается.", retryable=True)
    from .catalog import CatalogRepository

    catalog = CatalogRepository(settings.catalog_path)
    started = store.start(req, catalog.catalog_version, catalog.taxonomy_version)
    if isinstance(started, SearchResponse):
        return started
    request_id, state = started
    try:
        return _search(req, catalog, store, settings, request_id, state)
    except Exception:
        store.fail(request_id, state)
        raise


def _search(req, catalog, store, settings, request_id, state):
    start = monotonic()
    refresh = state.catalog_version != catalog.catalog_version or state.taxonomy_version != catalog.taxonomy_version
    if refresh:
        state = state.model_copy(update={
            "catalog_version": catalog.catalog_version, "taxonomy_version": catalog.taxonomy_version,
            "query_state": None, "recent_results": [], "seen_card_ids": [], "expert_plan": None,
        })

    def emit(kind, payload):
        store.emit(request_id, state, kind, payload)

    taxonomy = dict(catalog.taxonomy)
    taxonomy.update(request_id=request_id, selected_card_id=req.selected_card_id,
                    reference_request_id=str(req.reference_request_id) if req.reference_request_id else None)
    taxonomy["card_vendors"] = {
        card_id: card.vendor_ids for recent in state.recent_results for card_id in recent.display_order
        if (card := catalog.get_card(card_id)) is not None
    }
    resolver = getattr(catalog, "resolve_entities", None)
    if resolver:
        taxonomy["lookup"] = resolver(req.query)
    else:
        taxonomy["lookup"] = []
        for candidate in catalog.lookup(req.query):
            card = catalog.get_card(candidate.card_id)
            if card and norm(card.name) in norm(req.query):
                taxonomy["lookup"].append({
                    "type": "product", "text": card.name, "resolved_id": card.card_id,
                    "exact": norm(req.query) == norm(card.name),
                })
    rates = (settings.llm_input_rate, settings.llm_cached_input_rate, settings.llm_output_rate)
    pricing = None
    if all(rates) and settings.llm_pricing_version:
        pricing = Pricing(settings.llm_model, settings.llm_pricing_version,
                          "RUB" if settings.llm_provider == "yandex" else "USD", *map(Decimal, rates))
    interpreter = Interpreter(
        api_key=settings.api_key if settings.llm_state == "configured" else None,
        model=settings.llm_model, emit=emit, pricing=pricing, yandex_folder=settings.yandex_folder,
        timeout_seconds=settings.llm_timeout_seconds, max_calls=settings.llm_max_calls,
    )
    intent = interpreter.interpret(req.query, state, taxonomy, start + 18)
    query, delta = reduce_context(state, intent, request_id)
    emit("interpretation_completed", {
        "mode": intent.mode, "goal": None, "role_ids": intent.role_ids, "class_ids": intent.class_ids,
        "unresolved_constraints": ["unknown"] * len(intent.unresolved_constraints),
        "interpretation_source": intent.interpretation_source,
        "duration_ms": round((monotonic() - start) * 1000),
    })
    topic_id = str(uuid4()) if delta.topic_changed else state.topic_id
    emit("context_applied", {
        **delta.model_dump(), "topic_id": topic_id,
        "effective_filters": [{"key": f.key, "filter_id": f.filter_id} for f in query.filters],
    })
    plan = interpreter.plan_expert(intent, query, taxonomy, start + 22) if query.mode == "expert" else None
    if plan and not plan.clarification_question:
        plan.clarification_question = next(
            (part.unknowns[0] for part in plan.components if part.required and part.unknowns),
            "На какую модель и число одновременных пользователей рассчитано решение?"
            if "llm" in norm(query.goal) else None,
        )
    if plan:
        emit("expert_plan_completed", {
            "plan_id": plan.plan_id, "component_ids": [c.component_id for c in plan.components],
            "assumptions": [], "scope_limited": plan.scope_limited, "model_call_id": plan.model_call_id,
        })
    seen = [] if delta.topic_changed else state.seen_card_ids
    effective_query = query
    if intent.operation == "reference" and delta.resolved_reference and not intent.filter_changes:
        effective_query = query.model_copy(update={"entities": [
            Entity(text="Карточка из выдачи", type="product", usage="target", resolved_id=delta.resolved_reference)
        ]})
    matches = collect_matches(catalog, effective_query, plan, start + 25, emit,
                              limit=req.limit, seen_card_ids=seen)
    items = {}
    primary_items = []
    for match in matches.primary:
        card_id = match.candidate.card_id
        if card_id in items:
            component = match.candidate.component_id
            if (component is not None and match.match_level == "full"
                    and items[card_id].selected_offer_id == match.selected_offer.offer_id
                    and component not in items[card_id].component_ids):
                items[card_id].component_ids.append(component)
            continue
        items[card_id] = _item(catalog, match, query)
        primary_items.append(items[card_id])
    decision = apply_explicit_constraints(primary_items, query.filters)
    primary_items = decision.items[:req.limit]
    related_items = []
    shown = {item.card_id for item in primary_items}
    for related in matches.related:
        card_id = related.verified.candidate.card_id
        if card_id in shown or len(primary_items) + len(related_items) >= req.limit:
            continue
        related_items.append(_item(catalog, related.verified, query, related))
        shown.add(card_id)
    groups = {"primary_full": 0, "primary_partial": 1, "related": 2}
    results = sorted([*primary_items, *related_items], key=lambda item: groups[item.group])
    items = {item.card_id: item for item in results}
    companions = _companions(catalog, results)
    counts = Counts(**{key: sum(i.group == key for i in results) for key in Counts.model_fields})
    degraded = intent.interpretation_source == "degraded_rules" or (query.mode == "expert" and not plan)
    outcome = "full" if counts.primary_full else "partial" if counts.primary_partial else "none"
    if query.mode == "expert":
        required = {c.component_id for c in plan.components if c.required} if plan else set()
        covered = all(c.status == "covered" for c in matches.components if c.component_id in required)
        outcome = ("undetermined" if not plan else "full" if covered and matches.components
                   else "partial" if counts.primary_full or counts.primary_partial else "none")
    elif not results and (intent.clarification_question or degraded or matches.truncated):
        outcome = "undetermined"
    warnings = list(decision.notes)
    if not decision.notes and not counts.primary_full and any(item.budget_status in {"within", "over"} for item in results):
        warnings.append("В проверенных предложениях не подтвердилось полное совпадение в пределах бюджета.")
    warnings.extend(f"Условие «{value}» пока не проверяется по каталогу."
                    for value in unsupported_measurements(req.query))
    corrected = next((item["corrected_text"] for item in taxonomy["lookup"] if "corrected_text" in item), None)
    if corrected:
        warnings.append(f"Поиск с исправлением: {corrected}.")
    if any(entity.type == "vendor" and entity.usage == "target" for entity in query.entities):
        warnings.append("Поиск охватывает товары каталога. Разделы сайта и вебинары в выдачу не входят.")
    for condition, message in (
        (refresh, "Снимок каталога обновился. Диалог начат заново."),
        (degraded, "Внешняя интерпретация недоступна. Использованы локальные правила."),
        (matches.truncated, "Поиск ограничен проверенной выборкой."),
        (len(results) < 4, "В проверенной выборке меньше четырёх обоснованных позиций."),
    ):
        if condition:
            warnings.append(message)
    for condition, code in (
        (degraded, "llm_unavailable"), (not results and not degraded, "no_candidates"),
        (any(check.status == "mismatch" for item in results if item.group == "primary_partial"
             for check in item.unmet_conditions), "no_matching_offer"),
        (any(check.status == "unknown" for item in results if item.group == "primary_partial"
             for check in item.unmet_conditions), "missing_attributes"),
        ("unmapped_need" in query.unresolved_constraints, "need_unmapped"),
        (matches.truncated, "candidate_limit"),
        (decision.price_unmet, "explicit_budget_unmet"),
        (bool(decision.unmet_filter_ids), "explicit_constraint_unmet"),
    ):
        if condition:
            affected = {
                "explicit_budget_unmet": [f.filter_id for f in query.filters
                                          if is_budget_ceiling(f) and f.key == "price_minor"],
                "explicit_constraint_unmet": decision.unmet_filter_ids,
            }
            emit("search_issue", {"code": code, "scope": "request", "evidence_status": "observed",
                                  "affected_filter_ids": affected.get(code, []),
                                  "candidate_count": len(matches.primary)})
    visible = set(items)
    for component in matches.components:
        component.full_card_ids = [card_id for card_id in component.full_card_ids if card_id in visible]
        component.partial_card_ids = [card_id for card_id in component.partial_card_ids if card_id in visible]
    now = datetime.now(UTC)
    state = state.model_copy(update={
        "revision": state.revision + 1, "topic_id": topic_id, "mode": query.mode, "query_state": query,
        "goal": query.goal, "expert_plan": plan, "seen_card_ids": list(dict.fromkeys([*seen, *items, *(item.card_id for item in companions)]))[-200:],
        "recent_results": [*state.recent_results[-7:], RecentResult(
            request_id=request_id, display_order=[item.card_id for item in results]
        )],
        "updated_at": now.isoformat(), "expires_at": (now + timedelta(hours=12)).isoformat(),
    })
    suggestions = [Suggestion(label=f"Искать: {corrected}", action="query", query=corrected)] if corrected else []
    if not counts.primary_full and len(query.filters) > 1:
        suggestions.extend(
            Suggestion(label=f"Снять условие «{f.source_text}»", action="remove_filter", filter_id=f.filter_id)
            for f in query.filters[:3] if f.strength == "prefer"
        )
    response = SearchResponse(
        request_id=request_id, client_request_id=str(req.client_request_id), session_id=state.session_id,
        revision=state.revision, catalog_version=catalog.catalog_version, mode=query.mode,
        status="degraded" if degraded else "needs_clarification" if intent.clarification_question else "ok",
        interpreted_goal=query.goal, applied_filters=query.filters, context_delta=delta, items=results,
        companions=companions,
        components=matches.components, expert_assumptions=plan.assumptions if plan else [],
        primary_outcome=outcome, counts=counts, insufficient_relevant_items=len(results) < 4,
        search_truncated=matches.truncated, warnings=warnings, suggestions=suggestions,
        clarification_question=intent.clarification_question or (plan.clarification_question if plan else None)
        or ("Какую модель планируете запускать и сколько пользователей будут работать одновременно?"
            if query.mode == "expert" and not plan and any(f.key.startswith("vram_") for f in query.filters) else None),
        trace_url=f"/api/v1/trace/{request_id}",
        usage=store.usage(request_id, round((monotonic() - start) * 1000)),
        provenance=Provenance(catalog_data="provided"),
    )
    store.finish(response, state)
    return response


def _companions(catalog, results):
    anchor = next((item for item in results if item.group == "primary_full"), None)
    if anchor is None:
        anchor = next((item for item in results if item.group == "primary_partial"), None)
    if anchor is None:
        return []
    excluded = {item.card_id for item in results}
    return [
        CompanionCard(
            card_id=card.card_id, name=card.name,
            vendor_names=[catalog.vendor_name(vendor) or vendor for vendor in card.vendor_ids],
            kind=card.kind, class_id=card.class_id, image_url=card.image_url,
            role_label=label, url=_companion_url(catalog, card), anchor_card_id=anchor.card_id,
        )
        for label, card in select_companions(catalog, catalog.get_card(anchor.card_id), excluded)
    ]


def _companion_url(catalog, card):
    if card.card_url:
        return card.card_url
    return vendor_section_url(catalog.offer_url(card.card_id))


def _offer_price_label(offer, record):
    if offer.price:
        return f"{Decimal(offer.price.amount_minor) / 100:,.2f} {offer.price.currency}".replace(",", " ")
    return "Цена по запросу" if record and record.price_state == "on_request" else "Цена неизвестна"


def _offer_conditions(offer, filters):
    conditions = {f.filter_id: f for f in filters}
    matched = []
    unmet = []
    for check in offer.conditions:
        f = conditions.get(check.filter_id)
        if f and f.strength == "exclude":
            if check.status == "unknown":
                unmet.append(check)
        elif check.status == "match":
            matched.append(check)
        else:
            unmet.append(check)
    return matched, unmet


def _budget_reason(status, filters, offer):
    if status == "within":
        required = {f.filter_id for f in filters if f.strength == "require" and not is_budget_ceiling(f)}
        if any(c.status == "mismatch" and c.filter_id in required for c in offer.conditions):
            return "Цена предложения укладывается в бюджет. Отмеченные условия отличаются от запроса."
        return "Цена предложения укладывается в бюджет. Соответствие запросу подтверждено частично."
    if status == "over":
        return "Цена предложения выше бюджета. Остальные проверенные обязательные условия совпадают."
    checks = {c.filter_id: c for c in offer.conditions}
    price_checks = [checks[f.filter_id] for f in filters if is_budget_ceiling(f) and f.filter_id in checks]
    if any(c.reason_code == "unknown_quantity_basis" for c in price_checks):
        return "По данным каталога нельзя подтвердить стоимость нужного объёма закупки."
    if any(c.reason_code == "unknown_price" for c in price_checks):
        return "Цена для сравнения с бюджетом не подтверждена."
    if price_checks and all(c.status == "match" for c in price_checks):
        return "Цена предложения в бюджете. Часть обязательных характеристик не подтверждена."
    return "Предложение соответствует запросу частично. Проверьте цену и отмеченные условия."


def _budget_alternative(catalog, filters, match, status):
    if status not in {"within", "over"} or match.match_level == "full":
        return None
    opposite = "over" if status == "within" else "within"
    for offer in match.alternate_offers:
        alternative_status, difference = budget_fit(filters, offer)
        if alternative_status != opposite or misses_stated_requirement(filters, offer.conditions):
            continue
        record = catalog.get_offer(offer.offer_id)
        if record is None:
            continue
        matched, unmet = _offer_conditions(offer, filters)
        return BudgetAlternative(
            offer_id=offer.offer_id, name=record.name, price=offer.price,
            price_label=_offer_price_label(offer, record), available=offer.available, url=offer.url,
            budget_status=alternative_status, budget_difference=difference,
            matched_conditions=matched, unmet_conditions=unmet,
            reason=_budget_reason(alternative_status, filters, offer),
        )
    return None


def _item(catalog, match, query, related=None):
    card = catalog.get_card(match.candidate.card_id)
    offer = match.selected_offer
    record = catalog.get_offer(offer.offer_id)
    matched, unmet = _offer_conditions(offer, query.filters)
    status, difference = budget_fit(query.filters, offer) if related is None else (None, None)
    reason = "Подходит по проверенным условиям." if match.match_level == "full" else (
        _budget_reason(status, query.filters, offer) if status else "Условия подтверждены частично."
    )
    return ResultItem(
        card_id=card.card_id, name=card.name, vendor_names=[catalog.vendor_name(v) or v for v in card.vendor_ids],
        kind=card.kind, class_id=card.class_id, image_url=card.image_url,
        group="related" if related else "primary_full" if match.match_level == "full" else "primary_partial",
        match_level=match.match_level,
        component_ids=[match.candidate.component_id] if match.candidate.component_id else [],
        selected_offer_id=offer.offer_id, price=offer.price, quantity_total=offer.quantity_total,
        price_label=_offer_price_label(offer, record), available=offer.available,
        availability_label={True: "В наличии по снимку", False: "Нет в наличии по снимку",
                            None: "Наличие не подтверждено"}[offer.available],
        url=offer.url or card.card_url, url_kind="offer" if offer.url else "card" if card.card_url else "none",
        matched_conditions=matched, unmet_conditions=unmet, source_refs=offer.source_refs,
        reason=related.reason if related else reason,
        caveats=["Совместимость не проверена."] if related else
        (["Есть невыполненные или неподтверждённые условия."] if match.match_level == "partial" else []),
        relation_id=related.relation_id if related else None, compatibility_status="not_verified",
        selected_offer_name=record.name if status and record else None,
        budget_status=status, budget_difference=difference,
        budget_alternative=_budget_alternative(catalog, query.filters, match, status),
    )

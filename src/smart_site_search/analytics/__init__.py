import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from math import ceil
from statistics import median

from ..contracts.analytics import AnalyticsReport, Cohort, DialogueModeFilter, Demand, Gap, Metric, ModeCost, ReportMetric
from .dialogues import build_dialogue_report, is_mock, read_dialogue_rows


VAT_DIVISOR = Decimal("1.22")
RECOMMENDATIONS = {
    "missing_attributes": "Проверить данные по запрошенным характеристикам.",
    "no_matching_offer": "Проверить сочетания условий и ассортимент офферов.",
    "explicit_budget_unmet": "Явный ценовой лимит не выполнен: проверить пустую выдачу или запасной диапазон до 40%.",
    "explicit_constraint_unmet": "Явное условие не выполнено: проверить пустую выдачу или ближайшие варианты.",
    "no_candidates": "Проверить словарь ролей и покрытие каталога.",
    "candidate_limit": "Проверить полноту retrieval и доступный бюджет поиска.",
    "need_unmapped": "Уточнить роли и альтернативные формулировки потребности.",
    "llm_unavailable": "Проверить доступность внешней интерпретации.",
}


def build_report(
    store, start: datetime, end: datetime, cohort: str, *, mode: DialogueModeFilter = "all",
    issue: str | None = None, offset: int = 0, limit: int = 25,
) -> AnalyticsReport:
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    generated_at = datetime.now(UTC)
    with store.connection() as db:
        db.execute("BEGIN")
        requests = db.execute("""
            SELECT request_id, session_id, status, mode, response_json FROM requests
            WHERE started_at >= ? AND started_at < ? ORDER BY started_at
        """, (start.isoformat(), end.isoformat())).fetchall()
        rows = db.execute("""
            SELECT e.request_id, e.envelope_json FROM events e JOIN requests r USING (request_id)
            WHERE r.started_at >= ? AND r.started_at < ?
              AND json_extract(e.envelope_json, '$.received_at') < ?
              AND (e.event_type IN ('request_received','interpretation_completed','context_applied',
                   'search_issue','llm_call_started','llm_call_completed','response_completed',
                   'technical_error','request_interrupted','feedback','results_impression',
                   'result_click','mock_order')
                   OR json_extract(e.envelope_json, '$.provenance.behavior_mock')=1
                   OR json_extract(e.envelope_json, '$.provenance.profile_mock')=1
                   OR json_extract(e.envelope_json, '$.provenance.business_signals_mock')=1
                   OR json_extract(e.envelope_json, '$.provenance.replay')=1)
            ORDER BY e.request_id, e.seq
        """, (start.isoformat(), end.isoformat(), end.isoformat())).fetchall()
        dialogue_data = read_dialogue_rows(db, start, end, generated_at)
    events = defaultdict(list)
    for request_id, envelope in rows:
        events[request_id].append(json.loads(envelope))
    report = []
    for name in (("real", "mock") if cohort == "all" else (cohort,)):
        dialogues = build_dialogue_report(dialogue_data, name, mode=mode, issue=issue, offset=offset, limit=limit)
        report.append(_cohort(name, requests, events, dialogues))
    return AnalyticsReport.model_validate({
        "from": start.isoformat(), "to": end.isoformat(),
        "generated_at": generated_at.isoformat(), "cohorts": report,
    })


def _cohort(name, requests, events, dialogues):
    metrics = []

    def metric(key, definition, *, value=None, numerator=None, denominator=None, limit=()):
        if denominator:
            value = numerator / denominator
        metrics.append(ReportMetric(
            metric_id=key, metric=Metric(
                value=value, numerator=numerator, denominator=denominator, definition=definition,
                availability="unavailable" if value is None else "mock" if name == "mock" else "measured",
                limitations=list(limit),
            ),
        ))

    demand, demand_sessions, gaps = Counter(), defaultdict(set), Counter()
    outcomes, counts, mode_requests = Counter(), Counter(), Counter()
    calls = defaultdict(list)
    impressions, clicks, shown_sessions, order_sessions = set(), set(), set(), set()
    funnel_responses = set()
    latencies = []
    token_counts = defaultdict(list)
    purposes = Counter()
    for request in requests:
        request_id, session = request["request_id"], request["session_id"]
        response = json.loads(request["response_json"]) if request["response_json"] else {}
        trace = events[request_id]
        types = defaultdict(list)
        for event in trace:
            types[event["event_type"]].append(event)
        terminal = bool(types["response_completed"])
        request_is_mock = is_mock(response.get("provenance", {})) or any(
            is_mock(e["provenance"]) for e in trace if e["event_type"] != "mock_order"
        )
        belongs = request_is_mock == (name == "mock")
        if terminal and (belongs or name == "mock" and types["mock_order"]):
            funnel_responses.add(request_id)
            for event in types["results_impression"]:
                impressions |= {(request_id, item["card_id"]) for item in event["payload"]["visible_items"]}
                if event["payload"]["visible_items"]:
                    shown_sessions.add(session)
            for event in types["result_click"]:
                clicks.add((request_id, event["payload"]["card_id"]))
            if name == "mock" and types["mock_order"]:
                order_sessions.add(session)
        if not belongs or not types["request_received"]:
            continue
        counts["accepted"] += 1
        mode = response.get("mode") or "unknown"
        if types["interpretation_completed"]:
            intent = types["interpretation_completed"][-1]["payload"]
            mode = intent.get("mode") or mode
            for key in set(intent.get("role_ids", []) or intent.get("class_ids", []) or [mode]):
                demand[key] += 1
                demand_sessions[key].add(session)
        mode_requests[mode] += 1
        for event in types["search_issue"]:
            issue = event["payload"]
            gaps[issue["code"], issue["evidence_status"]] += 1
        completed = {item["payload"]["call_id"]: item["payload"] for item in types["llm_call_completed"]}
        for item in types["llm_call_started"]:
            usage = completed.get(item["payload"]["call_id"], {})
            calls[mode].append(usage)
            purpose = usage.get("purpose") or item["payload"].get("purpose")
            if purpose in {"interpret", "expert_plan"}:
                purposes[purpose] += 1
            for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
                if usage.get(key) is not None:
                    token_counts[key].append(usage[key])
        counts["errors"] += bool(types["technical_error"] or types["request_interrupted"])
        if terminal:
            outcomes[response["primary_outcome"]] += 1
            counts["empty"] += not response["items"]
            counts["related_only"] += bool(response["items"]) and all(
                item["group"] == "related" for item in response["items"]
            )
            counts["degraded"] += response["status"] == "degraded"
            counts["trace_complete"] += bool(types["interpretation_completed"] and types["context_applied"])
            latencies.append(response["usage"]["latency_ms"])
        if types["feedback"]:
            counts["feedback"] += 1
            counts["negative"] += types["feedback"][-1]["payload"]["rating"] == "not_useful"

    defined = sum(outcomes[key] for key in ("full", "partial", "none"))
    completed_count = sum(outcomes.values())
    for key in ("full", "partial", "none", "undetermined"):
        metric(f"primary_{key}_rate", "Доля исхода основной потребности среди подходящих запросов.",
               numerator=outcomes[key], denominator=counts["accepted"] if key == "undetermined" else defined)
    metric("empty_rate", "Доля ответов без карточек.", numerator=counts["empty"],
           denominator=completed_count)
    metric("related_only_rate", "Доля ответов только со связанными товарами.",
           numerator=counts["related_only"], denominator=completed_count)
    metric("shown_rate", "Доля ответов с показом карточек.",
           numerator=len({request_id for request_id, _ in impressions}), denominator=len(funnel_responses))
    metric("ctr", "Доля уникальных показанных пар запрос–карточка, по которым кликнули.",
           numerator=len(clicks & impressions), denominator=len(impressions))
    metric("order_conversion", "Сессии с атрибутированным заказом среди сессий с показом.",
           numerator=len(order_sessions & shown_sessions),
           denominator=len(shown_sessions) if name == "mock" else None,
           limit=("На стенде доступны только синтетические заказы.",))
    metric("exit_after_failure", "Доля сессий, завершённых после неудачного поиска.",
           limit=("Нет событий, позволяющих подтвердить завершение сессии.",))
    metric("lost_orders", "Оценка числа потерянных заказов.",
           limit=("Для оценки нужен отдельный сценарий с допущениями о конверсии.",))
    metric("p50_ms", "Медианная задержка завершённого поиска.", value=median(latencies) if latencies else None)
    metric("p95_ms", "95-й процентиль задержки, метод ближайшего ранга.",
           value=sorted(latencies)[ceil(0.95 * len(latencies)) - 1] if latencies else None)
    for key, definition, base in (
        ("degraded", "Доля завершённых ответов в режиме деградации.", completed_count),
        ("errors", "Доля ошибок и прерываний от принятых запросов.", counts["accepted"]),
        ("trace_complete", "Доля завершённых запросов с трассой интерпретации и контекста.", completed_count),
        ("negative", "Доля отрицательных оценок среди запросов с обратной связью.", counts["feedback"]),
    ):
        metric(f"{key}_rate", definition, numerator=counts[key], denominator=base)
    metric("interpret_calls", "Число внешних вызовов интерпретации запроса.",
           value=purposes["interpret"] or None)
    metric("expert_plan_calls", "Число внешних вызовов экспертного плана.",
           value=purposes["expert_plan"] or None)
    call_count = sum(map(len, calls.values()))
    for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
        values = token_counts[key]
        metric(key, "Сумма наблюдаемых токенов внешних вызовов.",
               value=sum(values) if values else None,
               limit=(f"Замеры доступны для {len(values)} из {call_count} вызовов.",))
    costs = []
    for mode, request_count in sorted(mode_requests.items()):
        by_currency = defaultdict(list)
        for usage in calls[mode]:
            by_currency[usage.get("currency") or "USD"].append(usage.get("cost"))
        for currency, amounts in sorted((by_currency or {"USD": []}).items()):
            known = sum((Decimal(value) for value in amounts if value is not None), Decimal(0))
            unknown_count = amounts.count(None)
            ex_vat = None
            if currency == "RUB":
                ex_vat = format((known / VAT_DIVISOR).quantize(Decimal("0.000001")), "f")
            costs.append(ModeCost(mode=mode, requests=request_count, calls=len(amounts),
                                  known_cost=format(known, "f"), known_cost_ex_vat=ex_vat,
                                  currency=currency, unknown_calls=unknown_count))
            metric(f"mean_cost_{mode}_{currency.lower()}",
                   f"Средняя стоимость внешних вызовов на запрос в режиме {mode}, {currency}.",
                   value=float(known / request_count) if not unknown_count else None,
                   limit=((f"Нет расчёта для {unknown_count} вызовов.",) if unknown_count else ()))
    return Cohort(
        name=name, metrics=metrics, costs_by_mode=costs, dialogues=dialogues,
        demand=[Demand(goal_or_role=key, requests=n, sessions=len(demand_sessions[key]))
                for key, n in demand.most_common(20)],
        gaps=[Gap(code=code, count=n, evidence_status=status,
                  recommendation=RECOMMENDATIONS.get(code, "Просмотреть обезличенную трассу поиска."))
              for (code, status), n in gaps.most_common()],
    )

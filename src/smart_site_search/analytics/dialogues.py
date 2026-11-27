"""Dialogue reports from recorded search responses and their existing event stream."""

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from statistics import median


OBSERVATION_MINUTES = 30
OBSERVATION = timedelta(minutes=OBSERVATION_MINUTES)
MODES = {"direct", "functional", "expert"}
OUTCOMES = {"full", "partial", "none", "undetermined"}
CHECKED_OUTCOMES = OUTCOMES - {"undetermined"}
PROBLEM_OUTCOMES = {"partial", "none"}
CATALOG_ISSUES = {"no_candidates", "no_matching_offer", "missing_attributes", "need_unmapped"}
BUDGET_KEYS = {"price_minor", "total_price_minor"}
MOCK_FLAGS = ("behavior_mock", "profile_mock", "business_signals_mock", "replay")

RECOMMENDATIONS = {
    "budget_over": "Сравнить цены подходящих предложений и заявленные лимиты.",
    "budget_unknown": "Проверить цену, валюту и единицу закупки в каталоге.",
    "candidate_limit": "Проверить, какие кандидаты не попали в проверенную выборку.",
    "llm_unavailable": "Проверить доступность внешней интерпретации.",
    "missing_attributes": "Проверить запрошенные характеристики у предложений в каталоге.",
    "need_unmapped": "Проверить, поддерживает ли словарь ролей формулировку потребности.",
    "no_candidates": "Проверить покрытие каталога и этап поиска кандидатов.",
    "no_matching_offer": "Сопоставить обязательные условия с доступными вариантами.",
    "repeated_problem": "Посмотреть, менялись ли результаты поиска при тех же обязательных условиях.",
    "request_interrupted": "Проверить серверные прерывания запроса.",
    "technical_error": "Проверить технический сбой запроса.",
}


@dataclass
class DialogueRows:
    start: datetime
    end: datetime
    generated_at: datetime
    requests: list
    events: dict


@dataclass
class Turn:
    detail: dict
    filters: dict
    requirements: frozenset | None
    budget_tags: set
    overages: dict
    role_ids: list
    class_ids: list
    is_mock: bool


def read_dialogue_rows(db, start, end, generated_at):
    """Read starts in the selected period and follow-ups through its observation horizon."""
    until = min(end + OBSERVATION, generated_at)
    bounds = (start.isoformat(), until.isoformat())
    requests = db.execute("""
        SELECT request_id, session_id, started_at, response_json
        FROM requests WHERE started_at >= ? AND started_at < ?
        ORDER BY started_at, request_id
    """, bounds).fetchall()
    events = defaultdict(list)
    for request_id, envelope in db.execute("""
        SELECT e.request_id, e.envelope_json FROM events e JOIN requests r USING (request_id)
        WHERE r.started_at >= ? AND r.started_at < ?
          AND json_extract(e.envelope_json, '$.received_at') < ?
          AND e.event_type <> 'mock_order'
          AND (e.event_type IN ('context_applied','interpretation_completed','response_completed',
               'search_issue','results_impression','result_click','feedback','technical_error','request_interrupted')
               OR json_extract(e.envelope_json, '$.provenance.catalog_data')='synthetic_fixture'
               OR json_extract(e.envelope_json, '$.provenance.behavior_mock')=1
               OR json_extract(e.envelope_json, '$.provenance.profile_mock')=1
               OR json_extract(e.envelope_json, '$.provenance.business_signals_mock')=1
               OR json_extract(e.envelope_json, '$.provenance.replay')=1)
        ORDER BY e.request_id, e.seq
    """, (*bounds, until.isoformat())):
        event = json.loads(envelope)
        events[request_id].append((datetime.fromisoformat(event["received_at"]), event))
    return DialogueRows(start, end, generated_at, requests, events)


def is_mock(provenance):
    return provenance.get("catalog_data") == "synthetic_fixture" or any(provenance.get(f) for f in MOCK_FLAGS)


def _key(value):
    return value if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) else None


def _codes(values):
    return sorted({key for value in values if (key := _key(value))})


def _mode(value):
    return value if value in MODES else "unknown"


def _latest(events, name):
    return next((event["payload"] for _, event in reversed(events) if event["event_type"] == name), {})


def _budget(item):
    key, op = item.get("key"), item.get("op")
    if key not in BUDGET_KEYS or op not in {"lte", "between"} or item.get("strength") != "require":
        return None
    values = item.get("values", [])
    currency = item.get("currency")
    if (len(values) != (2 if op == "between" else 1) or any(type(n) is not int or n < 0 for n in values)
            or not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency)):
        return None
    return {"key": key, "op": op, "values": values, "currency": currency}


def _mandatory(filters):
    signatures = set()
    for item in filters:
        if item.get("strength") not in {"require", "exclude"}:
            continue
        values = [json.dumps(value, ensure_ascii=False) for value in item.get("values", [])]
        if item.get("op") == "in":
            values.sort()
        signatures.add((item.get("key"), item.get("scope"), item.get("component_id"), item.get("op"),
                        item.get("strength"), item.get("currency"), item.get("unit"), tuple(values)))
    return frozenset(signatures)


def _money(value):
    if not isinstance(value, dict):
        return None
    amount, currency = value.get("amount_minor"), value.get("currency")
    if type(amount) is int and amount >= 0 and isinstance(currency, str) and re.fullmatch(r"[A-Z]{3}", currency):
        return {"amount_minor": amount, "currency": currency}
    return None


def _offers(items, clicks):
    for item in items:
        group = item.get("group")
        if group not in {"primary_full", "primary_partial", "related"}:
            continue
        variants = [(item, False)]
        if item.get("budget_alternative"):
            variants.append((item["budget_alternative"], True))
        for variant, alternative in variants:
            offer_id = variant.get("offer_id" if alternative else "selected_offer_id")
            status = variant.get("budget_status")
            yield {
                "card_name": item.get("name") or "Товар", "offer_name": (
                    variant.get("name" if alternative else "selected_offer_name") or None
                ),
                "group": group, "price": _money(variant.get("price")),
                "budget_status": status if status in {"within", "over", "unconfirmed"} else None,
                "budget_difference": _money(variant.get("budget_difference")), "alternative": alternative,
                "clicked": bool(offer_id and (item.get("card_id"), offer_id) in clicks),
            }


def _budget_observations(budget_ids, offers, items, outcome):
    tags, overages = set(), {}
    if not budget_ids:
        return tags, overages
    if outcome == "full":
        tags.add("budget_full")
    for offer in offers:
        if offer["group"] != "primary_partial":
            continue
        if offer["budget_status"] == "within":
            tags.add("budget_within")
        if offer["budget_status"] == "over" and outcome != "full":
            tags.add("budget_over")
            if difference := offer["budget_difference"]:
                amount, currency = difference["amount_minor"], difference["currency"]
                if amount > 0:
                    overages[currency] = min(amount, overages.get(currency, amount))
    for item in items:
        if item.get("group") == "related":
            continue
        for candidate in (item, item.get("budget_alternative") or {}):
            if any(check.get("filter_id") in budget_ids and check.get("status") == "unknown"
                   for check in candidate.get("unmet_conditions", [])):
                tags.add("budget_unknown")
    return tags, overages


def _turn(row, events, previous_filters):
    context = _latest(events, "context_applied")
    intent = _latest(events, "interpretation_completed")
    kinds = {event["event_type"] for _, event in events}
    complete = "response_completed" in kinds and bool(row["response_json"])
    response = json.loads(row["response_json"]) if complete else {}
    primary_outcome = response.get("primary_outcome")
    if complete:
        outcome = primary_outcome if primary_outcome in OUTCOMES else "undetermined"
    else:
        outcome = "error" if kinds.intersection({"technical_error", "request_interrupted"}) else "pending"
    filters = response.get("applied_filters", [])
    current = {f["filter_id"]: f.get("key") for f in context.get("effective_filters", []) if "filter_id" in f}
    current.update({f["filter_id"]: f.get("key") for f in filters if "filter_id" in f})
    before_and_after = {**previous_filters, **current}
    changes = {
        action: _codes(before_and_after.get(identifier) for identifier in context.get(f"{action}_filter_ids", []))
        for action in ("added", "replaced", "removed")
    }
    changes["added"] = sorted(set(changes["added"]) - set(changes["replaced"]))
    budget_filters = [(f.get("filter_id"), data) for f in filters if (data := _budget(f))]
    budget_ids = {identifier for identifier, _ in budget_filters if identifier}
    clicks = {(event["payload"].get("card_id"), event["payload"].get("offer_id"))
              for _, event in events if event["event_type"] == "result_click"}
    items = response.get("items", [])
    offers = list(_offers(items, clicks))
    tags, overages = _budget_observations(budget_ids, offers, items, outcome)
    issues = {event["payload"].get("code") for _, event in events if event["event_type"] == "search_issue"}
    issues.update(kinds.intersection({"technical_error", "request_interrupted"}))
    if outcome == "full":
        issues.difference_update(CATALOG_ISSUES)
    else:
        issues.update(tags.intersection({"budget_over", "budget_unknown"}))
    feedback = _latest(events, "feedback").get("rating")
    detail = {
        "started_at": row["started_at"], "mode": _mode(response.get("mode") or intent.get("mode")),
        "outcome": outcome, "clarification": bool(response.get("clarification_question")),
        "filter_keys": _codes(current.values()), "filter_changes": changes,
        "budgets": [value for _, value in budget_filters], "offers": offers,
        "impression_recorded": any(event["payload"].get("visible_items") for _, event in events
                                   if event["event_type"] == "results_impression"),
        "feedback": feedback if feedback in {"useful", "not_useful"} else None,
        "issues": sorted(issues.intersection(RECOMMENDATIONS)),
    }
    return Turn(detail, current, _mandatory(filters) if complete and "applied_filters" in response else None,
                tags, overages, _codes(intent.get("role_ids", [])), _codes(intent.get("class_ids", [])),
                is_mock(response.get("provenance", {})) or any(is_mock(e["provenance"]) for _, e in events))


def _topic(identifier, rows, started, data):
    deadline = started + OBSERVATION
    cutoff = min(deadline, data.generated_at)
    turns = []
    filters = {}
    for row in rows:
        row_start = datetime.fromisoformat(row["started_at"])
        if not started <= row_start < cutoff:
            continue
        events = [(at, e) for at, e in data.events[row["request_id"]] if at < cutoff]
        if not any(e["event_type"] == "context_applied" and e["payload"].get("topic_id") == identifier
                   for _, e in events):
            continue
        turn = _turn(row, events, filters)
        turns.append(turn)
        filters = turn.filters
    if not turns:
        return None
    first = turns[0]
    outcomes = [turn.detail["outcome"] for turn in turns]
    recovery = None
    if outcomes[0] in PROBLEM_OUTCOMES and first.requirements is not None:
        resolved = next((turn for turn in turns[1:] if turn.detail["outcome"] == "full"
                         and turn.requirements is not None), None)
        if resolved is not None:
            recovery = "preserved" if first.requirements <= resolved.requirements else "changed"
    for earlier, later in zip(turns, turns[1:]):
        if (earlier.detail["outcome"] in PROBLEM_OUTCOMES and earlier.detail["outcome"] == later.detail["outcome"]
                and earlier.requirements is not None and earlier.requirements == later.requirements):
            later.detail["issues"] = sorted({*later.detail["issues"], "repeated_problem"})
    report = {
        "topic_id": sha256(identifier.encode()).hexdigest()[:16], "started_at": started.isoformat(),
        "mode": first.detail["mode"], "first_outcome": outcomes[0], "last_outcome": outcomes[-1],
        "observing": data.generated_at < deadline, "recovery": recovery,
        "role_ids": sorted({role for turn in turns for role in turn.role_ids}),
        "class_ids": sorted({cls for turn in turns for cls in turn.class_ids}),
        "issues": sorted({issue for turn in turns for issue in turn.detail["issues"]}),
        "turns": [turn.detail for turn in turns],
    }
    return report, turns


def build_dialogue_report(data, cohort, *, mode, issue, offset, limit):
    rows_by_topic = defaultdict(list)
    starts = {}
    unattributed = 0
    for row in data.requests:
        events = data.events[row["request_id"]]
        context = _latest(events, "context_applied")
        started = datetime.fromisoformat(row["started_at"])
        identifier = context.get("topic_id")
        if not identifier:
            if started < data.end:
                turn = _turn(row, events, {})
                unattributed += turn.is_mock == (cohort == "mock") and mode in {"all", turn.detail["mode"]}
            continue
        key = row["session_id"], identifier
        rows_by_topic[key].append(row)
        if started < data.end and context.get("topic_changed") is True:
            starts.setdefault(key, started)

    topics, overages = [], defaultdict(list)
    counts = Counter()
    problems = Counter()
    trend = defaultdict(Counter)
    for key, started in starts.items():
        observed = _topic(key[1], rows_by_topic[key], started, data)
        if observed is None:
            continue
        topic, turns = observed
        if any(turn.is_mock for turn in turns) != (cohort == "mock") or mode not in {"all", topic["mode"]}:
            continue
        topics.append(topic)
        problems.update(topic["issues"])
        daily = trend[started.date().isoformat()]
        daily["topics"] += 1
        if topic["first_outcome"] in CHECKED_OUTCOMES:
            counts["first_checked"] += 1
            full = topic["first_outcome"] == "full"
            counts["first_full"] += full
            daily["first_full"] += full
        tags = set().union(*(turn.budget_tags for turn in turns))
        counts["budget_topics"] += any(turn.detail["budgets"] for turn in turns)
        counts.update(tags)
        last_feedback = next((turn.detail["feedback"] for turn in reversed(turns) if turn.detail["feedback"]), None)
        if last_feedback:
            counts["feedback_topics"] += 1
            counts["helpful_topics"] += last_feedback == "useful"
        counts["offer_click_topics"] += any(offer["clicked"] for turn in turns for offer in turn.detail["offers"])
        counts["alternative_click_topics"] += any(offer["clicked"] and offer["alternative"]
                                                  for turn in turns for offer in turn.detail["offers"])
        topic_overages = {}
        for turn in turns:
            for currency, amount in turn.overages.items():
                topic_overages[currency] = min(amount, topic_overages.get(currency, amount))
        for currency, amount in topic_overages.items():
            overages[currency].append(amount)
        if topic["observing"]:
            counts["observing"] += 1
            continue
        if topic["first_outcome"] in PROBLEM_OUTCOMES:
            counts["recovery_base"] += 1
            if topic["recovery"]:
                counts["recovered"] += 1
                counts[f"recovered_{topic['recovery']}"] += 1
                daily["recovered"] += 1
        clarification = next((index for index, turn in enumerate(turns) if turn.detail["clarification"]), None)
        if clarification is not None:
            counts["clarification"] += 1
            followups = turns[clarification + 1:]
            counts["clarification_followup"] += bool(followups)
            counts["clarification_full"] += any(turn.detail["outcome"] == "full" for turn in followups)
        counts["repeated_problem"] += "repeated_problem" in topic["issues"]
        counts["repeat_base"] += len(turns) > 1

    metrics = _metrics(counts, overages, len(topics), cohort)
    eligible = [topic for topic in topics if not issue or issue in topic["issues"]]
    eligible.sort(key=lambda topic: (topic["started_at"], topic["topic_id"]), reverse=True)
    return {
        "observation_minutes": OBSERVATION_MINUTES, "unattributed_requests": unattributed,
        "topics_total": len(topics), "filtered_total": len(eligible), "offset": offset, "limit": limit,
        "metrics": metrics,
        "trend": [{"date": date, **{key: counts[key] for key in ("topics", "first_full", "recovered")}}
                  for date, counts in sorted(trend.items())],
        "problems": [{"code": code, "count": count, "recommendation": RECOMMENDATIONS[code]}
                     for code, count in problems.most_common()],
        "topics": eligible[offset:offset + limit],
    }


def _metrics(counts, overages, total, cohort):
    metrics = []

    def add(key, definition, *, value=None, numerator=None, denominator=None, limitations=()):
        if denominator:
            value = numerator / denominator
        metrics.append({"metric_id": key, "metric": {
            "value": value, "numerator": numerator, "denominator": denominator, "definition": definition,
            "availability": "unavailable" if value is None else "mock" if cohort == "mock" else "measured",
            "limitations": list(limitations),
        }})

    observation = ("Учитываются темы с истёкшим окном наблюдения 30 минут от первого запроса.",)
    add("topics", "Темы, начатые за выбранный период, с записанным началом.", value=total)
    add("observing_topics", "Темы с открытым окном наблюдения.", value=counts["observing"])
    add("first_full_rate", "Полное совпадение в первом ответе среди тем с проверяемым первым исходом.",
        numerator=counts["first_full"], denominator=counts["first_checked"],
        limitations=("Исходы без подтверждённого результата проверки в знаменатель не входят.",))
    add("recovery_rate", "Появилось полное совпадение после частичного или пустого первого результата.",
        numerator=counts["recovered"], denominator=counts["recovery_base"], limitations=observation)
    add("recovered_preserved", "При первом полном совпадении исходные обязательные условия и исключения сохранились.",
        value=counts["recovered_preserved"], limitations=observation)
    add("recovered_changed", "При первом полном совпадении часть исходных обязательных условий изменилась или снята.",
        value=counts["recovered_changed"], limitations=observation)
    add("clarification_followup_rate", "В той же теме поступила реплика после первого уточняющего вопроса.",
        numerator=counts["clarification_followup"], denominator=counts["clarification"],
        limitations=(*observation, "Смысловой ответ на конкретный вопрос отдельно не записывается."))
    add("clarification_full_rate", "Появилось полное совпадение в продолженных после уточнения темах.",
        numerator=counts["clarification_full"], denominator=counts["clarification_followup"], limitations=observation)
    add("repeated_problem_rate", "Тот же частичный или пустой исход повторился подряд при тех же обязательных условиях.",
        numerator=counts["repeated_problem"], denominator=counts["repeat_base"], limitations=observation)
    add("helpful_rate", "Доля положительных последних оценок среди тем, в которых оставили оценку.",
        numerator=counts["helpful_topics"], denominator=counts["feedback_topics"],
        limitations=("Оценки доступны только на экранах, где записывается обратная связь.",))
    for key, definition in (
        ("offer_click_topics", "Темы с записанным переходом к офферу."),
        ("alternative_click_topics", "Темы с записанным переходом к альтернативному офферу."),
    ):
        add(key, definition, value=counts[key], limitations=(
            "Считаются записанные события. Виджет не отправляет событие перехода к офферу.",
        ))
    add("budget_topics", "Темы с записанным обязательным верхним пределом цены.", value=counts["budget_topics"])
    for key, definition in (
        ("budget_full", "Темы с полным совпадением при действующем лимите."),
        ("budget_within", "Темы с бюджетным предложением из частичной выдачи."),
        ("budget_over", "Темы с подходящим по проверенным условиям предложением дороже лимита."),
        ("budget_unknown", "Темы с неподтверждённой ценой, валютой или основой расчёта."),
    ):
        add(f"{key}_topics", definition, value=counts[key], limitations=(
            "Одна тема может попасть в несколько групп. Учтены наблюдавшиеся ответы и статусы в их сохранённых данных.",
        ))
    for currency, amounts in sorted(overages.items()):
        add(f"median_over_budget_{currency.lower()}", f"Медиана наименьшего превышения цены по темам, {currency}.",
            value=median(amounts) / 100, limitations=(f"Тем с записанной суммой превышения: {len(amounts)}.",))
    return metrics

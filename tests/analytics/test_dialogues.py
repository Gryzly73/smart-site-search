import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from smart_site_search.analytics.dialogues import build_dialogue_report, read_dialogue_rows


START = datetime(2026, 11, 25, 8, tzinfo=UTC)
END = START + timedelta(hours=1)


@pytest.fixture
def db():
    schema = Path(__file__).parents[2] / "src/smart_site_search/runtime_schema.sql"
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript(schema.read_text(encoding="utf-8"))
        yield connection


def add_event(db, request, session, moment, kind, payload, provenance=None):
    seq = db.execute("SELECT COUNT(*) FROM events WHERE request_id=?", (request,)).fetchone()[0] + 1
    envelope = {"received_at": moment.isoformat(), "event_type": kind, "payload": payload,
                "provenance": provenance or {"catalog_data": "provided"}}
    db.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
               (f"{request}-{seq}", request, session, seq, kind, moment.isoformat(), json.dumps(envelope)))


def add_turn(db, request, session, minute, *, topic=None, fresh=False, mode="functional", outcome="partial",
             filters=(), changed=None, items=(), question=False, finish_minute=None, synthetic=False,
             goal=None, warnings=()):
    moment = START + timedelta(minutes=minute)
    finished = START + timedelta(minutes=finish_minute) if finish_minute is not None else moment + timedelta(seconds=1)
    db.execute("INSERT OR IGNORE INTO sessions VALUES (?, 0, ?, '{}')", (session, END.isoformat()))
    response = {
        "primary_outcome": outcome, "mode": mode, "applied_filters": list(filters), "items": list(items),
        "provenance": {"catalog_data": "synthetic_fixture" if synthetic else "provided"},
        "interpreted_goal": "Нужен бэкап для виртуальных машин" if goal is None else goal,
        "trace_url": "/api/private-trace", "warnings": list(warnings),
        "clarification_question": "Какую задачу резервного копирования нужно закрыть?" if question else None,
    }
    db.execute("""INSERT INTO requests(request_id, session_id, client_request_id, creates_session, payload_hash,
                                       status, started_at, completed_at, mode, response_json)
                  VALUES (?, ?, ?, 0, 'opaque', 'complete', ?, ?, ?, ?)""",
               (request, session, request, moment.isoformat(), finished.isoformat(), mode, json.dumps(response)))
    add_event(db, request, session, moment, "interpretation_completed", {
        "mode": mode, "role_ids": ["backup_restore", "private raw role"],
    })
    if topic:
        add_event(db, request, session, moment, "context_applied", {
            "topic_id": topic, "topic_changed": fresh,
            "effective_filters": [{"filter_id": f["filter_id"], "key": f["key"]} for f in filters],
            **(changed or {}),
        })
    add_event(db, request, session, finished, "response_completed", {"primary_outcome": outcome})


def budget(identifier, amount):
    return {"filter_id": identifier, "key": "price_minor", "op": "lte", "values": [amount * 100],
            "currency": "RUB", "strength": "require", "scope": "topic", "source_text": "private filter text"}


def report(db, *, generated_at=END + timedelta(hours=1), cohort="real", mode="all", issue=None, offset=0, limit=25):
    data = read_dialogue_rows(db, START, END, generated_at)
    return build_dialogue_report(data, cohort, mode=mode, issue=issue, offset=offset, limit=limit)


def metrics(report):
    return {entry["metric_id"]: entry["metric"] for entry in report["metrics"]}


def test_budget_topic_keeps_followups_beyond_period_and_uses_alternative_click(db):
    first, second = budget("filter-1", 90_000), budget("filter-2", 110_000)
    items = [{"card_id": "license-card", "name": "Кибер Бэкап", "selected_offer_id": "education", "group": "primary_partial",
              "price": {"amount_minor": 8_775_700, "currency": "RUB"}, "budget_status": "within",
              "budget_alternative": {"offer_id": "commercial", "name": "Коммерческая лицензия", "budget_status": "over",
                                     "price": {"amount_minor": 10_323_600, "currency": "RUB"},
                                     "budget_difference": {"amount_minor": 1_323_600, "currency": "RUB"}}}]
    add_turn(db, "private-start-request", "private-session", 50, topic="private-topic", fresh=True,
             filters=[first], items=items, question=True, changed={"added_filter_ids": [first["filter_id"]]})
    add_event(db, "private-start-request", "private-session", START + timedelta(minutes=51), "result_click",
              {"card_id": "license-card", "offer_id": "commercial"})
    add_event(db, "private-start-request", "private-session", START + timedelta(minutes=52), "feedback",
              {"rating": "useful", "reason": "private feedback text"})
    add_turn(db, "private-followup-request", "private-session", 65, topic="private-topic", filters=[second],
             outcome="full", changed={"replaced_filter_ids": [first["filter_id"]], "added_filter_ids": [second["filter_id"]]})
    add_turn(db, "private-late-request", "private-session", 82, topic="private-topic", outcome="none")

    data = report(db, issue="budget_over")
    assert data["topics_total"] == data["filtered_total"] == 1
    topic, = data["topics"]
    assert topic["role_ids"] == ["backup_restore"]
    assert not topic["observing"]
    assert (topic["first_outcome"], topic["last_outcome"], topic["recovery"]) == ("partial", "full", "changed")
    assert len(topic["turns"]) == 2
    original, updated = topic["turns"]
    assert original["offers"][0]["clicked"] is False
    assert original["offers"][1]["clicked"] is True
    assert original["request_text"] == "Нужен бэкап для виртуальных машин"
    assert original["answer_text"] == "Какую задачу резервного копирования нужно закрыть?"
    assert original["result_kind"] == "clarification"
    assert original["filter_changes"] == {"added": ["price_minor"], "replaced": [], "removed": []}
    assert updated["filter_changes"] == {"added": [], "replaced": ["price_minor"], "removed": []}
    assert updated["budgets"][0]["values"] == [11_000_000]
    assert "private" not in json.dumps(data)
    values = metrics(data)
    assert values["recovery_rate"]["value"] == values["clarification_full_rate"]["value"] == 1
    assert values["median_over_budget_rub"]["value"] == 13_236
    assert values["helpful_rate"]["value"] == 1
    assert values["alternative_click_topics"]["value"] == 1


def test_fresh_window_and_terminal_time_do_not_leak_a_future_response(db):
    one = budget("first-budget", 15_000)
    same_limit_new_filter_id = budget("future-budget", 15_000)
    add_turn(db, "fresh-1", "demo", 45, topic="fresh", fresh=True, filters=[one])
    add_turn(db, "fresh-2", "demo", 55, topic="fresh", filters=[same_limit_new_filter_id],
             outcome="full", finish_minute=71)

    snapshot = report(db, generated_at=END)
    topic, = snapshot["topics"]
    assert topic["observing"] and topic["last_outcome"] == "pending"
    assert topic["turns"][-1]["budgets"] == []
    assert metrics(snapshot)["recovery_rate"]["denominator"] == 0

    mature = report(db)
    topic, = mature["topics"]
    assert not topic["observing"] and topic["recovery"] == "preserved"
    assert metrics(mature)["recovered_preserved"]["value"] == 1


def test_old_continuations_and_unattributed_requests_do_not_create_topics(db):
    add_turn(db, "old-1", "old-session", -10, topic="old-topic", fresh=True)
    add_turn(db, "old-2", "old-session", 10, topic="old-topic", outcome="full")
    add_turn(db, "unattributed", "new-session", 20)
    add_turn(db, "end-1", "end-session", 60, topic="end-topic", fresh=True)
    data = report(db)
    assert data["topics_total"] == data["filtered_total"] == 0
    assert data["unattributed_requests"] == 1


def test_mock_orders_leave_real_topics_in_place_and_issue_filter_preserves_totals(db):
    add_turn(db, "first-real", "alpha", 5, topic="real-line", fresh=True, mode="direct", outcome="full")
    add_event(db, "first-real", "alpha", START + timedelta(minutes=6), "mock_order", {}, {"behavior_mock": True})
    add_event(db, "first-real", "alpha", START + timedelta(minutes=6), "search_issue", {"code": "no_matching_offer"})
    add_turn(db, "second-real", "beta", 8, topic="review-line", fresh=True, mode="direct", outcome="none")
    add_event(db, "second-real", "beta", START + timedelta(minutes=9), "search_issue", {"code": "no_candidates"})
    add_turn(db, "first-mixed", "gamma", 10, topic="mock-line", fresh=True)
    add_turn(db, "second-mixed", "gamma", 11, topic="mock-line", synthetic=True)

    real = report(db, issue="no_candidates", mode="direct")
    assert real["topics_total"] == 2
    assert real["filtered_total"] == 1
    assert real["problems"] == [{"code": "no_candidates", "count": 1,
                                  "recommendation": "Проверить покрытие каталога и этап поиска кандидатов."}]
    assert metrics(real)["first_full_rate"]["value"] == 0.5
    assert not report(db, issue="no_candidates", offset=1)["topics"]
    demo = report(db, cohort="mock")
    assert demo["topics_total"] == 1
    assert demo["topics"][0]["issues"] == ["repeated_problem"]
    assert metrics(demo)["topics"]["availability"] == "mock"


def test_customer_summary_uses_stated_condition_and_shown_answer(db):
    stretch = "Товара в вашем ценовом диапазоне нет, но можем предложить решения до 40% выше указанной цены."
    closest = "Точного совпадения по условию «видеокарта от 200 гб» нет, можем предложить ближайшие варианты."
    price = {"filter_id": "price", "key": "price_minor", "op": "lte", "values": [5_000_000],
             "currency": "RUB", "strength": "require", "scope": "topic", "source_text": "цена до 50000"}
    memory = {"filter_id": "vram", "key": "vram_gb", "op": "gte", "values": [200],
              "strength": "require", "scope": "topic", "source_text": "видеокарта от 200 гб"}
    card = {"card_id": "gpu", "name": "NVIDIA H200 NVL", "selected_offer_id": "h200",
            "group": "primary_partial", "price": {"amount_minor": 1, "currency": "RUB"}, "budget_status": "within"}
    add_turn(db, "price-request", "buyer", 10, topic="price-topic", fresh=True, mode="direct",
             outcome="partial", goal="functional", filters=[price], warnings=[stretch],
             items=[{"card_id": "laptop", "name": "Ноутбук Lenovo", "selected_offer_id": "lenovo",
                     "group": "primary_partial", "budget_status": "over",
                     "price": {"amount_minor": 6_000_000, "currency": "RUB"}}])
    add_turn(db, "vram-request", "buyer-2", 20, topic="vram-topic", fresh=True,
             outcome="partial", goal="functional", filters=[memory], warnings=[closest], items=[card])
    add_turn(db, "empty-request", "buyer-3", 30, topic="empty-topic", fresh=True, outcome="none",
             goal="functional", filters=[price], warnings=["Товара в вашем ценовом диапазоне нет."])
    add_turn(db, "coded-request", "buyer-0", 5, topic="coded-topic", fresh=True, outcome="partial",
             goal="unmapped_need", question=True)
    add_turn(db, "exact-request", "buyer-4", 40, topic="exact-topic", fresh=True, outcome="full",
             goal="Сервер без сетевых карт", items=[{
                 "card_id": "server", "name": "INFERIT RS212", "selected_offer_id": "rs",
                 "group": "primary_full", "price": {"amount_minor": 1, "currency": "RUB"},
             }])

    data = report(db)
    assert data["summary"] == [
        {"kind": "exact", "count": 1},
        {"kind": "price_near", "count": 1},
        {"kind": "characteristic_near", "count": 1},
        {"kind": "unavailable", "count": 1},
        {"kind": "clarification", "count": 1},
    ]
    coded = next(topic for topic in data["topics"] if topic["result_kind"] == "clarification")
    assert coded["turns"][0]["request_text"] == ""
    assert "unmapped_need" not in json.dumps(data)
    assert data["trend"][0]["date"] == "2026-11-25"
    by_kind = {example["result_kind"]: example for example in data["examples"]}
    assert by_kind["price_near"]["request_text"] == "цена до 50000"
    assert "до 40%" in by_kind["price_near"]["answer_text"]
    assert by_kind["price_near"]["product_name"] == "Ноутбук Lenovo"
    assert by_kind["characteristic_near"]["request_text"] == "видеокарта от 200 гб"
    assert by_kind["characteristic_near"]["product_name"] == "NVIDIA H200 NVL"
    assert by_kind["unavailable"]["request_text"] == "цена до 50000"
    assert by_kind["unavailable"]["product_name"] is None
    assert by_kind["exact"]["request_text"] == "Сервер без сетевых карт"
    assert by_kind["exact"]["answer_text"] == "Показаны товары, которые подошли по запросу."
    assert "private" not in json.dumps(data)

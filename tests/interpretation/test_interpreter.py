import asyncio
import json
import time
from decimal import Decimal

import httpx
import pytest

from smart_site_search.contracts.intent import QueryState, SearchSession
from smart_site_search.interpretation import DEFAULT_MODEL, Interpreter, Pricing
from smart_site_search.session import reduce_context
from smart_site_search.taxonomy import load_taxonomy


def payload(**overrides):
    return dict(dict(mode="functional", operation="new_topic", goal="Восстановить рабочие файлы",
                     role_ids=["backup_restore"], class_ids=[], search_terms=["резервное копирование"],
                     entities=[], filter_changes=[], reference_ordinal=None, unresolved_constraints=[],
                     clarification_question=None), **overrides)


def response(content, usage=None):
    return httpx.Response(200, headers={"x-request-id": "req_fixture"}, json=dict(
        status="completed", output=[{"type": "message", "role": "assistant", "content":[
            {"type":"output_text","text":json.dumps(content, ensure_ascii=False)}]}],
        usage=usage))


def interpreter(handler=None, **kwargs):
    events = []
    obj = Interpreter(api_key="synthetic-test-key", transport=httpx.MockTransport(handler or unexpected),
                      emit=lambda event, data: events.append((event, data)), **kwargs)
    return obj, events


def unexpected(req):
    pytest.fail("unexpected provider request")


def interpret(obj, query="Как вернуть данные после отказа?", **taxonomy):
    return obj.interpret(query, None, load_taxonomy() | taxonomy, time.monotonic() + 2)


def test_direct_whole_match_skips_provider_partial_name_does_not():
    obj, events = interpreter()
    lookup = [{"type": "product", "text": "Fixture Linux", "resolved_id": "group:f", "exact": True}]
    got = interpret(obj, "Fixture Linux", lookup=lookup)
    assert (got.interpretation_source, got.mode, got.entities[0].resolved_id) == ("rules", "direct", "group:f")
    assert events == []
    lookup[0]["exact"] = False
    obj, events = interpreter(lambda r: httpx.Response(401))
    got = interpret(obj, "Fixture Linux на год", lookup=lookup)
    assert got.interpretation_source == "degraded_rules"
    assert got.entities[0].resolved_id == "group:f"
    assert got.filter_changes[0].filter.values == [12]
    assert [x[0] for x in events] == ["llm_call_started", "llm_call_completed"]


def test_structured_request_usage_redaction_and_role_mapping():
    usage = {"input_tokens": 100, "input_tokens_details": {"cached_tokens": 10},
             "output_tokens": 20, "output_tokens_details": {"reasoning_tokens": 7}}
    def handler(req):
        body = json.loads(req.content)
        assert str(req.url) == "https://api.openai.com/v1/responses"
        assert body["text"]["format"]["strict"] and not body["store"]
        assert body["max_output_tokens"] == 2000 and body["reasoning"]["effort"] == "none"
        assert "example.test" not in body["input"] and "sk-fakecredential" not in body["input"]
        return response(payload(), usage)
    obj, events = interpreter(handler)
    got = interpret(obj, "Как вернуть документы? mail@example.test пароль: sk-fakecredential")
    assert got.interpretation_source == "llm" and got.role_ids == ["backup_restore"]
    event = events[-1][1]
    assert event["status"] == "success" and event["provider_request_id"] == "req_fixture"
    assert Decimal(event["cost"]) == Decimal("0.00015825") and event["reasoning_tokens"] == 7
    assert "mail@" not in repr(events) and "sk-" not in repr(events) and "документы" not in repr(events)


@pytest.mark.parametrize("bad", [
    payload(role_ids=["invented"]), payload(class_ids=["invented"]),
    payload(**{"price":100}), payload(entities=[{"text":"Invented", "type":"product", "usage":"target"}]),
    payload(filter_changes=[{"action":"remove", "scope":"session", "key":"vendor_id", "filter":None}]),
])
def test_invalid_model_result_falls_back_with_usage(bad):
    obj, events = interpreter(lambda req: response(bad))
    result = interpret(obj, "антивирь на 50 компов с неизвестным требованием")
    assert result.interpretation_source == "degraded_rules"
    assert result.role_ids == ["endpoint_security"]
    assert result.filter_changes[0].filter.key == "devices" and result.unresolved_constraints
    assert events[-1][1]["status"] == "invalid_output" and events[-1][1]["cost"] is None


def test_provider_errors_timeout_and_refusal():
    obj, events = interpreter(lambda req: httpx.Response(429))
    assert interpret(obj).interpretation_source == "degraded_rules"
    assert [e[1]["attempt"] for e in events if e[0] == "llm_call_completed"] == [1, 2]
    assert interpret(obj).interpretation_source == "degraded_rules" and len(events) == 4
    obj, events = interpreter(lambda req: httpx.Response(200, json={"status":"completed","output":[
        {"type":"message","role":"assistant","content":[{"type":"refusal","refusal":"raw private text"}]}]}))
    assert interpret(obj).interpretation_source == "degraded_rules" and events[-1][1]["status"] == "refused"
    async def timeout(req):
        await asyncio.sleep(10)
        return response(payload())
    obj, events = interpreter(timeout)
    start = time.monotonic()
    got = obj.interpret("локальная LLM", None, load_taxonomy(), start + 0.1)
    assert got.interpretation_source == "degraded_rules" and time.monotonic()-start < 1.5
    assert events[-1][1]["status"] == "timeout"


def test_unknown_tariff_and_explicit_override():
    usage = {"input_tokens":10,"input_tokens_details":{"cached_tokens":1},"output_tokens":1}
    obj, events = interpreter(lambda req: response(payload(), usage), model="other")
    interpret(obj)
    assert events[-1][1]["cost_status"] == "unknown"
    p = Pricing("other","fixture","USD",Decimal(1),Decimal("0.1"),Decimal(2))
    obj, events = interpreter(lambda req: response(payload(), usage), model="other", pricing=p)
    interpret(obj)
    assert Decimal(events[-1][1]["cost"]) == Decimal("0.0000111")


def test_real_plan_adapter_two_stages_over_mock_transport():
    query = "Нужен локальный запуск с GPU от 48 ГБ"
    component = dict(label="Вычислитель", required=True, role_ids=["gpu"], search_terms=["ускоритель"],
                     filters=[dict(key="vram_gb",op="gte",values=[48],unit="GB",currency=None,
                                   strength="require",source_text="GPU от 48 ГБ")],
                     rationale="Выполнять вычисления.", unknowns=["Какая модель?"])
    def handler(req):
        body = json.loads(req.content)
        if body["reasoning"]["effort"] == "none":
            return response(payload(mode="expert", goal=query, role_ids=[]))
        assert body["max_output_tokens"] == 4000
        return response(dict(scope_limited=False, assumptions=[], clarification_question=None, components=[component]))
    obj, events = interpreter(handler)
    got = interpret(obj, query)
    plan = obj.plan_expert(got, QueryState(goal=query, mode="expert"), load_taxonomy(), time.monotonic()+1)
    assert plan.model_call_id == events[-1][1]["call_id"] and plan.knowledge_source == "external_llm"
    assert plan.components[0].filters[0].component_id == plan.components[0].component_id
    assert len(events) == 4 and events[-1][1]["model"] == DEFAULT_MODEL


def test_without_key_or_expired_deadline_has_no_call():
    obj, events = interpreter()
    obj.api_key = None
    assert interpret(obj).interpretation_source == "degraded_rules"
    obj.api_key = "synthetic-test-key"
    obj.interpret("операционная система", None, load_taxonomy(), time.monotonic()-1)
    assert events == []


@pytest.mark.parametrize(("suffix", "field", "amount", "currency"), [
    ("до 15000", "price_minor", 1_500_000, "RUB"),
    ("строго до 15\u00a0000", "price_minor", 1_500_000, "RUB"),
    ("бюджет: 15 тыс. руб.", "price_minor", 1_500_000, "RUB"),
    ("до 15к", "price_minor", 1_500_000, "RUB"),
    ("до 1,5 млн", "price_minor", 150_000_000, "RUB"),
    ("не дороже 6000,50 ₽", "price_minor", 600_050, "RUB"),
    ("до $1500", "price_minor", 150_000, "USD"),
    ("до 1500 евро", "price_minor", 150_000, "EUR"),
    ("общий бюджет 15000", "total_price_minor", 1_500_000, "RUB"),
    ("до 15000 за весь комплект", "total_price_minor", 1_500_000, "RUB"),
    ("до 15000 за одну лицензию", "price_minor", 1_500_000, "RUB"),
])
def test_direct_product_budget(suffix, field, amount, currency):
    obj, events = interpreter()
    lookup = [{"type": "product", "text": "Fixture Linux", "resolved_id": "group:f", "exact": False}]
    result = interpret(obj, f"Fixture Linux {suffix}", lookup=lookup)
    budget, = [change.filter for change in result.filter_changes]
    assert (budget.key, budget.values, budget.currency) == (field, [amount], currency)
    assert budget.op == "lte" and budget.strength == "require"
    assert result.interpretation_source == "rules" and not events


@pytest.mark.parametrize("query", [
    "Fixture Linux до 15 000 пользователей",
    "Fixture Linux до 32 ГБ",
    "Fixture Linux до 32 K",
    "Fixture Linux до 15000 с",
    "Fixture Linux до 2026",
    "Fixture Linux до 01.05",
    "Fixture Linux до 01.05.2026",
    "Fixture Linux версии до 15.4",
    "Fixture Linux до 15000 CAD",
    "до 15000",
])
def test_bare_number_outside_price_context_does_not_set_budget(query):
    lookup = [{"type": "product", "text": "Fixture Linux", "resolved_id": "group:f", "exact": False}]
    result = interpret(Interpreter(), query, lookup=lookup)
    assert not any(change.key in ("price_minor", "total_price_minor") for change in result.filter_changes)


def test_budget_does_not_reuse_a_number_from_a_quantity_clause():
    result = interpret(Interpreter(), "Антивирус до 15 000 пользователей, бюджет 30 000 рублей")
    filters = {change.key: change.filter for change in result.filter_changes}
    assert (filters["users"].values, filters["users"].op) == ([15000], "lte")
    assert filters["price_minor"].values == [3_000_000]


def test_bare_budget_followup_refines_the_current_topic():
    session = SearchSession(session_id="s", revision=1, topic_id="t", catalog_version="test",
                            taxonomy_version="test", updated_at="2026-11-27", expires_at="2026-11-28",
                            query_state=QueryState(goal="ноутбук", mode="functional", role_ids=["laptop"]))
    obj, events = interpreter()
    result = obj.interpret("Тогда до 15 000", session, load_taxonomy(), time.monotonic() + 2)
    query, delta = reduce_context(session, result, "r2")
    assert result.operation == "refine" and not delta.topic_changed and not events
    assert query.goal == "ноутбук" and query.role_ids == ["laptop"]
    budget, = query.filters
    assert (budget.key, budget.values, budget.currency) == ("price_minor", [1_500_000], "RUB")


def test_new_product_defaults_to_rubles_and_followup_keeps_explicit_total():
    obj, events = interpreter()
    linux = dict(type="product", text="Fixture Linux", resolved_id="group:f", exact=False)
    first = interpret(obj, "Fixture Linux общий бюджет до $900", lookup=[linux])
    session = SearchSession(session_id="s", revision=1, topic_id="t", catalog_version="test",
                            taxonomy_version="test", updated_at="2026-11-27", expires_at="2026-11-28")
    session.query_state, _ = reduce_context(session, first, "r1")

    refine = obj.interpret("не дороже 15000", session, load_taxonomy(), time.monotonic() + 2)
    refined_query, _ = reduce_context(session, refine, "r2")
    refined_budget, = refined_query.filters
    assert (refined_budget.key, refined_budget.values, refined_budget.currency) == (
        "total_price_minor", [1_500_000], "USD",
    )

    office = dict(type="product", text="Fixture Office", resolved_id="group:o", exact=False)
    next_topic = obj.interpret("Fixture Office до 15000", session, load_taxonomy() | {"lookup": [office]},
                               time.monotonic() + 2)
    new_query, delta = reduce_context(session, next_topic, "r3")
    new_budget, = new_query.filters
    assert delta.topic_changed and not events
    assert (new_budget.key, new_budget.values, new_budget.currency) == ("price_minor", [1_500_000], "RUB")

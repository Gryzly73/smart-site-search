import time

from smart_site_search.contracts.intent import RecentResult, SearchSession
from smart_site_search.interpretation import Interpreter
from smart_site_search.session import reduce_context
from smart_site_search.taxonomy import load_taxonomy


def session():
    return SearchSession(session_id="s", revision=0, topic_id="t", catalog_version="test", taxonomy_version="test",
                         updated_at="2026-11-27", expires_at="2026-11-28")


def advance(s, query, rid, **tax):
    intent = Interpreter().interpret(query, s, load_taxonomy() | tax, time.monotonic()+1)
    before = s.model_dump()
    state, delta = reduce_context(s, intent, rid)
    assert s.model_dump() == before
    assert (state, delta) == reduce_context(s, intent, rid)
    s.query_state = state
    return intent, state, delta


def test_followup_changes_one_filter_and_new_topic_drops_it():
    s = session()
    lookup = [{"type":"product","text":"Fixture Linux","resolved_id":"group:f","exact":True}]
    advance(s, "Fixture Linux", "r1", lookup=lookup)
    _, state, delta = advance(s, "на год для юрлица", "r2")
    assert {f.key: f.values for f in state.filters} == {"term_months":[12], "customer_type":["legal_entity"]}
    assert state.entities[0].resolved_id == "group:f" and len(delta.added_filter_ids) == 2
    term = state.filters[0].filter_id
    _, state, delta = advance(s, "теперь на два года", "r3")
    assert state.filters[0].values == [24] and state.filters[0].source_turn_id == "r3"
    assert delta.replaced_filter_ids == [term]
    _, state, delta = advance(s, "срок неважен", "r4")
    assert [f.key for f in state.filters] == ["customer_type"] and len(delta.removed_filter_ids) == 1
    _, state, delta = advance(s, "теперь ноутбук", "r5")
    assert delta.topic_changed and not state.filters and not state.entities and "laptop" in state.role_ids


def test_concrete_previous_order_exclude_vendor_and_stale_reference():
    s = session()
    advance(s, "антивирус", "r1")
    s.recent_results = [RecentResult(request_id="r1", display_order=["group:x", "group:y"])]
    _, state, delta = advance(s, "второй вариант", "r2")
    assert delta.resolved_reference == state.reference_card_id == "group:y"
    _, state, _ = advance(s, "ещё без этого вендора", "r3", card_vendors={"group:y":["v-fixture"]})
    assert state.omit_seen and state.excluded_vendor_ids == ["v-fixture"]
    before = state.model_dump()
    intent, state, delta = advance(s, "третий вариант", "r4", reference_request_id="r1")
    assert intent.ambiguity == "needs_clarification" and before == state.model_dump()
    intent, state, _ = advance(s, "первый вариант", "r5", reference_request_id="stale")
    assert intent.ambiguity == "needs_clarification" and state.reference_card_id == "group:y"
    _, state, _ = advance(s, "теперь ноутбук", "r6")
    assert not state.excluded_vendor_ids and not state.reference_card_id and not state.omit_seen


def test_session_scope_survives_topic_change():
    s = session()
    advance(s, "во всех следующих запросах в наличии", "r1")
    _, state, _ = advance(s, "ноутбук", "r2")
    assert [(f.scope, f.key) for f in state.filters] == [("session", "available")]

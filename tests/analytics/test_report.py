from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from smart_site_search.analytics import build_report
from smart_site_search.api.app import create_app
from smart_site_search.contracts.api import SearchRequest, SearchResponse
from smart_site_search.contracts.events import ClientEvent
from smart_site_search.settings import Settings
from smart_site_search.telemetry import ApiError, RuntimeStore
from smart_site_search.telemetry.client import accept_event


FIXTURES = Path(__file__).parents[1] / "fixtures" / "contracts"


def test_usage_and_funnel_keep_mock_orders_out_of_real_conversion(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    req = SearchRequest.model_validate_json((FIXTURES / "search-request.json").read_text(encoding="utf-8"))
    start = datetime.now(UTC) - timedelta(minutes=1)
    request_id, state = store.start(req, "catalog-v1", "tax-v1")
    store.emit(request_id, state, "interpretation_completed", {
        "mode": "functional", "role_ids": ["backup_restore"],
    })
    store.emit(request_id, state, "llm_call_started", {"call_id": "uncertain"})
    response = SearchResponse.model_validate_json(
        (FIXTURES / "search-functional.json").read_text(encoding="utf-8")
    )
    response = response.model_copy(update={
        "request_id": request_id, "session_id": state.session_id,
        "catalog_version": state.catalog_version,
        "provenance": response.provenance.model_copy(update={"catalog_data": "provided"}),
    })
    store.finish(response, state.model_copy(update={"revision": 1}))
    card = response.items[0]
    position = {"card_id": card.card_id, "group": card.group, "position": 1}

    def event(kind, payload):
        return ClientEvent(
            event_id=str(uuid4()), session_id=state.session_id, request_id=request_id,
            occurred_at=datetime.now(UTC), event_type=kind, payload=payload,
        )

    impression = event("results_impression", {"request_id": request_id, "visible_items": [position]})
    assert accept_event(store, impression, True) == accept_event(store, impression, True)
    with pytest.raises(ApiError) as invalid:
        accept_event(store, event("result_click", {**position, "offer_id": "invented"}), True)
    assert invalid.value.status == 422
    for _ in range(2):
        accept_event(store, event("result_click", {**position, "offer_id": card.selected_offer_id}), True)
    order = event("mock_order", {
        "order_id": "demo", "attributed_request_id": request_id, "card_ids": [card.card_id],
        "attribution_method": "demo",
    })
    with pytest.raises(ApiError):
        accept_event(store, order, False)
    accept_event(store, order, True)
    real, mock = build_report(store, start, datetime.now(UTC) + timedelta(seconds=1), "all").cohorts
    metrics = {m.metric_id: m.metric for m in real.metrics}
    assert metrics["ctr"].numerator == metrics["ctr"].denominator == 1
    assert metrics["order_conversion"].value is None
    assert real.costs_by_mode[0].unknown_calls == 1
    mock_metrics = {m.metric_id: m.metric for m in mock.metrics}
    assert mock_metrics["order_conversion"].value == 1
    assert not mock.costs_by_mode


def test_analytics_endpoint_lists_topics_and_shows_the_customer_result(tmp_path):
    settings = replace(Settings.from_env(), runtime_path=tmp_path / "report.sqlite", external_llm_enabled=False)
    sample = SearchResponse.model_validate_json((FIXTURES / "search-functional.json").read_text(encoding="utf-8"))
    private_text = "dialogue-transcript-is-not-a-report-field"
    customer_query = "Нужен сервер до 100000"
    customer_answer = "Вы ищете оборудование или программное обеспечение?"
    start = datetime.now(UTC) - timedelta(minutes=1)

    with TestClient(create_app(settings)) as client:
        store = client.app.state.store
        original_ids = []
        for mode in ("direct", "functional", "direct"):
            request = SearchRequest(schema_version="1.0", client_request_id=uuid4(),
                                    expected_revision=0, query=customer_query)
            request_id, state = store.start(request, "catalog-v1", "tax-v1")
            original_ids.extend((request_id, state.session_id, state.topic_id))
            store.emit(request_id, state, "interpretation_completed", {"mode": mode, "role_ids": ["backup_restore"]})
            store.emit(request_id, state, "context_applied", {"topic_id": state.topic_id, "topic_changed": True})
            store.emit(request_id, state, "search_issue", {"code": "missing_attributes", "evidence_status": "observed"})
            response = sample.model_copy(update={
                "request_id": request_id, "session_id": state.session_id, "mode": mode,
                "interpreted_goal": customer_query, "clarification_question": customer_answer,
                "trace_url": f"/api/{private_text}",
                "primary_outcome": "partial", "provenance": sample.provenance.model_copy(
                    update={"catalog_data": "provided"}
                ),
            })
            store.finish(response, state.model_copy(update={"revision": 1}))

        params = {"from": start.isoformat(), "to": (datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
                  "cohort": "real", "mode": "direct", "issue": "missing_attributes", "limit": 1, "offset": 1}
        response = client.get("/api/v1/analytics", params=params)
        assert response.status_code == 200, response.text
        cohort, = response.json()["cohorts"]
        assert any(metric["metric_id"] == "ctr" for metric in cohort["metrics"])
        dialogues = cohort["dialogues"]
        assert dialogues["topics_total"] == dialogues["filtered_total"] == 2
        assert dialogues["offset"] == dialogues["limit"] == 1
        topic, = dialogues["topics"]
        assert topic["mode"] == "direct" and topic["observing"]
        turn, = topic["turns"]
        assert turn["clarification"] and "missing_attributes" in topic["issues"]
        assert turn["request_text"] == customer_query
        assert turn["answer_text"] == customer_answer
        assert private_text not in response.text
        assert all(identifier not in response.text for identifier in original_ids)
        assert client.get("/api/v1/analytics", params=params | {"offset": -1}).status_code == 422
        assert client.get("/analytics").status_code == 200

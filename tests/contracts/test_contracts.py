import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from smart_site_search.api import create_app
from smart_site_search.contracts.analytics import AnalyticsReport
from smart_site_search.contracts.api import ErrorResponse, SearchRequest, SearchResponse
from smart_site_search.contracts.events import EventEnvelope, LlmCallUsage
from smart_site_search.settings import Settings
from smart_site_search.telemetry import ApiError, RuntimeStore

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "contracts"


def read(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(("name", "kind"), [
    ("search-request", SearchRequest),
    ("search-functional", SearchResponse),
    ("search-expert", SearchResponse),
    ("search-degraded", SearchResponse),
    ("trace", list[EventEnvelope]),
    ("analytics", AnalyticsReport),
    ("error-revision", ErrorResponse),
])
def test_examples_follow_contract(name, kind):
    TypeAdapter(kind).validate_python(read(name))


def test_http_startup_and_validation(tmp_path):
    with TestClient(create_app(Settings(tmp_path / "catalog.sqlite", tmp_path / "state.sqlite"))) as client:
        assert client.get("/healthz").json() == {"status": "alive"}
        assert client.get("/readyz").json() == {
            "ready": False, "catalog_version": None, "storage_ok": True,
            "llm_state": "missing_key",
        }
        req = read("search-request")
        response = client.post("/api/v1/search", json={**req, "unexpected": "private text"})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
        assert "private text" not in response.text
        assert client.post("/api/v1/search", json=req).status_code == 503
        assert "/api/v1/search" in client.get("/openapi.json").json()["paths"]
        page = client.get("/widget")
        assert page.status_code == 200
        assert 'id="launcher"' in page.text
        assert "index.html" not in page.headers.get("content-disposition", "")


def test_runtime_replay_and_usage(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    req = SearchRequest.model_validate({**read("search-request"), "client_request_id": str(uuid4())})
    request_id, session = store.start(req, "synthetic-ui", "1")
    with pytest.raises(ApiError, match="Повторите") as busy:
        store.start(req, "synthetic-ui", "1")
    assert busy.value.code == "session_busy"

    store.emit(request_id, session, "llm_call_started", {"call_id": "call-1"})
    usage = LlmCallUsage(
        call_id="call-1", provider="test", model="test", purpose="interpret", attempt=1,
        status="invalid_output", usage_source="provider", input_tokens=10, output_tokens=3,
        cost="0.0000123", cost_status="calculated", currency="USD", latency_ms=5,
    )
    store.emit(request_id, session, "llm_call_completed", usage.model_dump())
    store.emit(request_id, session, "llm_call_started", {"call_id": "call-2"})
    totals = store.usage(request_id, 10)
    assert (totals.llm_attempts, totals.cost_known, totals.cost_complete) == (2, "0.0000123", False)

    session = session.model_copy(update={"revision": 1})
    response = SearchResponse.model_validate({
        **read("search-degraded"), "request_id": request_id, "session_id": session.session_id,
        "client_request_id": str(req.client_request_id), "usage": totals.model_dump(),
    })
    store.finish(response, session)
    assert store.start(req, "synthetic-ui", "1") == response
    events = store.trace(request_id)
    assert events[0].event_type == "request_received"
    assert events[-1].event_type == "response_completed"
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert req.query not in events[0].model_dump_json()
    with pytest.raises(ApiError) as conflict:
        store.start(req.model_copy(update={"query": "Другая фраза"}), "synthetic-ui", "1")
    assert conflict.value.code == "idempotency_conflict"


def test_invalid_new_session_revision():
    with pytest.raises(ValidationError):
        SearchRequest.model_validate({**read("search-request"), "expected_revision": 1})

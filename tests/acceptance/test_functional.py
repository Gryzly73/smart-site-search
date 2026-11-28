"""Offline acceptance tests using the shipped catalog and isolated state."""
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from functional_probe import SCENARIOS, compact, search
from smart_site_search.api.app import create_app
from smart_site_search.catalog import CatalogRepository
from smart_site_search.contracts.catalog import Card, Money, Offer
from smart_site_search.contracts.intent import Filter, QueryState
from smart_site_search.contracts.search import Candidate, CandidateBatch
from smart_site_search.search import collect_matches
from smart_site_search.search.companions import vendor_section_url
from smart_site_search.settings import Settings

CATALOG = Path(__file__).resolve().parents[2] / "data/catalog/catalog.sqlite"


@pytest.fixture
def client(tmp_path):
    if not CATALOG.is_file():
        pytest.skip("Acceptance tests require the built catalog snapshot.")
    settings = replace(Settings.from_env(), catalog_path=CATALOG, runtime_path=tmp_path / "state.sqlite",
                       demo_mode=True, external_llm_enabled=False)
    with TestClient(create_app(settings)) as client:
        yield client


def test_astra_card_uses_one_real_offer(client):
    repo = CatalogRepository(CATALOG)
    code, result = search(client, "Astra Linux Special Edition")
    assert code == 200
    first = result["items"][0]
    assert first["card_id"] == "group:121231"
    assert first["group"] == "primary_full"
    assert compact(first, repo)["snapshot_agreement"]
    assert len({item["card_id"] for item in result["items"]}) == len(result["items"])
    assert result["usage"]["llm_attempts"] == 0
    assert result["usage"]["cost_complete"]


def test_budget_comparison_uses_two_real_offers_on_one_card(client):
    request, raise_budget = SCENARIOS["A08_R04"]
    code, result = search(client, request)
    assert code == 200
    position, item = next((n, item) for n, item in enumerate(result["items"], 1)
                          if item["card_id"] == "group:173614")
    assert item["budget_status"] == "over"
    assert item["selected_offer_id"] == "421468"
    assert item["price"]["amount_minor"] == 10_323_600
    assert item["budget_alternative"] is None
    filters = {f["filter_id"]: f["key"] for f in result["applied_filters"]}
    assert [(filters[c["filter_id"]], c["status"]) for c in item["unmet_conditions"]] == [
        ("price_minor", "mismatch"),
    ]
    assert any("до 40%" in warning for warning in result["warnings"])
    click = client.post("/api/v1/events", json={
        "event_id": str(uuid4()), "event_type": "result_click",
        "session_id": result["session_id"], "request_id": result["request_id"],
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": {"card_id": item["card_id"], "offer_id": item["selected_offer_id"],
                    "group": item["group"], "position": position},
    })
    assert click.status_code == 202, click.text
    code, adjusted = search(client, raise_budget, previous=result)
    assert code == 200
    item = next(item for item in adjusted["items"] if item["card_id"] == "group:173614")
    assert item["selected_offer_id"] == "421468"
    assert item["budget_status"] == "within" and item["budget_alternative"] is None


def test_bare_budget_is_used_by_the_catalog_search(client):
    code, result = search(client, "Astra Linux Special Edition до 15000")
    assert code == 200
    assert [(f["key"], f["op"], f["values"], f["currency"]) for f in result["applied_filters"]] == [
        ("price_minor", "lte", [1_500_000], "RUB"),
    ]
    assert result["items"][0]["budget_status"] == "within"


def test_expert_fallback_verifies_requested_gpu_even_without_a_model(client):
    code, response = search(client, "Локальная LLM для сотрудников, доступный GPU от 48 ГБ")
    assert code == 200
    assert (response["mode"], response["status"], response["primary_outcome"]) == (
        "expert", "degraded", "undetermined",
    )
    assert {item["key"]: item["values"] for item in response["applied_filters"]} == {
        "available": [True], "vram_gb": [48],
    }
    gpu = response["components"][0]
    assert gpu["label"] == "Видеокарта"
    assert gpu["status"] == "partial"
    assert gpu["full_card_ids"] == []
    assert gpu["partial_card_ids"]
    assert all(item["group"] == "primary_partial" and item["unmet_conditions"] for item in response["items"])
    vram_id = next(item["filter_id"] for item in response["applied_filters"] if item["key"] == "vram_gb")
    available_memory = []
    unavailable_memory = []
    for item in response["items"]:
        check = next((condition for condition in [*item["matched_conditions"], *item["unmet_conditions"]]
                      if condition["filter_id"] == vram_id), None)
        if check is None or not check["actual_values"]:
            continue
        memory = max(value for value in check["actual_values"] if type(value) is int)
        if memory < 48:
            assert item["group"] != "primary_full"
        if check["status"] == "mismatch" and item["available"] is True:
            available_memory.append(memory)
        if check["status"] == "match" and item["available"] is False and memory >= 48:
            unavailable_memory.append(memory)
    assert available_memory
    assert max(available_memory) == 24
    assert not unavailable_memory
    assert any("ближайшие варианты" in warning for warning in response["warnings"])


def test_server_software_disambiguation_keeps_hardware_out_of_primary(client):
    code, initial = search(client, "сервер")
    assert code == 200 and initial["clarification_question"]
    code, software = search(client, "именно ПО для сервера", previous=initial)
    assert code == 200
    assert software["items"]
    assert all(item["kind"] == "software" for item in software["items"] if item["group"] != "related")


def test_products_without_a_companion_rule_add_no_companion_payload(client):
    code, response = search(client, "видеокарта")
    assert code == 200
    assert response["items"]
    assert response["companions"] == []
    assert response["counts"]["related"] == 0


def test_unverified_output_voltage_prevents_full_match(client):
    code, response = search(client, "ИБП с выходным напряжением 220 В")
    assert code == 200
    assert response["items"]
    assert response["counts"]["primary_full"] == 0
    assert any("220 в" in warning for warning in response["warnings"])


def test_reference_uses_saved_result_and_rejects_foreign_request(client):
    code, server = search(client, "Стойковый INFERIT RS212 и что к нему докупить")
    assert code == 200
    assert server["items"][0]["selected_offer_id"] == "542026"
    assert server["items"][0]["group"] == "primary_full"
    assert server["counts"]["related"] == 0
    assert [item["role_label"] for item in server["companions"]] == [
        "Серверная операционная система", "Виртуализация", "Резервное копирование",
    ]
    assert [item["card_id"] for item in server["companions"]] == [
        "group:408335", "group:409875", "group:173615",
    ]
    assert all("selected_offer_id" not in item for item in server["companions"])
    repo = CatalogRepository(CATALOG)
    for item in server["companions"]:
        card = repo.get_card(item["card_id"])
        assert card.card_url is None
        assert item["url"] == vendor_section_url(repo.offer_url(item["card_id"]))
        assert item["url"].count("/") == 4
    assert all(item["compatibility_status"] == "not_verified" for item in server["items"])
    code, notebook = search(client, "теперь ноутбук", previous=server)
    assert code == 200
    code, reference = search(client, "второй вариант", previous=notebook, reference=server)
    assert code == 200
    assert reference["context_delta"]["resolved_reference"] == server["items"][1]["card_id"]

    code, foreign = search(client, "Astra Linux Special Edition")
    assert code == 200
    code, error = search(client, "второй вариант", previous=reference, reference=foreign)
    assert code == 422
    assert error["error"]["code"] == "invalid_reference"


def event_flow(client):
    start = datetime.now(UTC) - timedelta(seconds=1)
    _, result = search(client, "Astra Linux Special Edition")
    _, empty = search(client, "Продукт Невозможный Сапсан ZZQX 76539")
    top = result["items"][0]
    pos = {key: top[key] for key in ("card_id", "group")} | {"position": 1}
    click = {**pos, "offer_id": top["selected_offer_id"]}
    rid = result["request_id"]
    statuses = {}

    def send(event_type, payload, target=result, existing_id=None):
        event_id = existing_id or str(uuid4())
        reply = client.post("/api/v1/events", json={
            "event_type": event_type, "payload": payload, "event_id": event_id,
            "request_id": target["request_id"], "session_id": target["session_id"],
            "occurred_at": datetime.now(UTC).isoformat(),
        })
        return event_id, reply.status_code

    event_id, statuses["impression"] = send("results_impression", {
        "request_id": rid, "visible_items": [pos],
    })
    _, statuses["duplicate_impression"] = send(
        "results_impression", {"request_id": rid, "visible_items": [pos]}, existing_id=event_id,
    )
    _, statuses["ui_click_with_nested_request_id"] = send("result_click", {**click, "request_id": rid})
    _, statuses["ui_feedback_with_nested_request_id"] = send(
        "feedback", {"rating": "not_useful", "request_id": rid},
    )
    _, statuses["wrong_offer_click"] = send("result_click", {**click, "offer_id": "invented"})
    _, statuses["wrong_position_click"] = send("result_click", {**click, "position": 2})
    _, statuses["corrected_click"] = send("result_click", click)
    _, statuses["second_corrected_click"] = send("result_click", click)
    _, statuses["corrected_negative_feedback"] = send("feedback", {"rating": "not_useful"})
    _, statuses["mock_order"] = send("mock_order", {
        "order_id": "acceptance-mock-order", "attributed_request_id": rid,
        "card_ids": [top["card_id"]], "attribution_method": "acceptance_mock",
    })
    _, statuses["empty_feedback"] = send("feedback", {"rating": "not_useful"}, target=empty)
    _, statuses["synthetic_click_with_provenance_flag"] = send(
        "result_click", {**click, "behavior_mock": True},
    )

    trace = client.get(result["trace_url"]).json()
    report = client.get("/api/v1/analytics", params={
        "from": start.isoformat(), "to": (datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
        "cohort": "all",
    })
    return {
        "statuses": statuses,
        "trace": [{"type": x["event_type"], "seq": x["seq"], "payload": x["payload"],
                   "provenance": x["provenance"]} for x in trace],
        "report": report.json(),
    }


def test_analytics_event_attribution_deduplicates_impression_and_click(client):
    run = event_flow(client)
    status = run["statuses"]
    assert status["impression"] == status["duplicate_impression"] == 202
    assert status["ui_click_with_nested_request_id"] == status["ui_feedback_with_nested_request_id"] == 202
    assert status["wrong_offer_click"] == status["wrong_position_click"] == 422
    assert status["corrected_click"] == status["second_corrected_click"] == 202
    assert status["mock_order"] == 202
    assert sum(event["type"] == "results_impression" for event in run["trace"]) == 1
    seq = [event["seq"] for event in run["trace"]]
    assert seq == sorted(set(seq))
    real, mock = run["report"]["cohorts"]
    real_metrics = {v["metric_id"]: v["metric"] for v in real["metrics"]}
    mock_metrics = {v["metric_id"]: v["metric"] for v in mock["metrics"]}
    assert real_metrics["ctr"]["value"] == 1
    assert real_metrics["ctr"]["denominator"] == 1
    assert real_metrics["shown_rate"]["denominator"] == 2
    assert real_metrics["empty_rate"]["value"] == 0.5
    assert real_metrics["order_conversion"]["availability"] == "unavailable"
    assert mock_metrics["order_conversion"]["availability"] == "mock"
    assert mock_metrics["order_conversion"]["value"] == 1


class PagedOffers:
    """Synthetic retrieval fixture, with real offer verification on each returned card."""

    def __init__(self, size=25, first_match=25):
        self.size = size
        self.first_match = first_match

    def retrieve(self, query, cursor, limit):
        start = int(cursor) if cursor else 0
        stop = min(start + limit, self.size)
        return CandidateBatch(
            candidates=[
                Candidate(card_id=f"offer:{n}", channels=["role"], channel_ranks=[],
                          matched_role_ids=[], evidence=[], relevance="confirmed")
                for n in range(start + 1, stop + 1)
            ],
            next_cursor=str(stop) if stop < self.size else None,
            examined_card_count=stop, exhausted=stop == self.size, truncated=False,
        )

    def get_card(self, card_id):
        return Card(card_id=card_id, kind="hardware", name=card_id, vendor_ids=[],
                    class_id="hardware", roles=[], shelf_ids=[], offer_count=1, available_count=1,
                    facet_values=[], catalog_version="acceptance-fixture")

    def get_offers(self, card_id):
        offer_id = card_id.split(":")[1]
        return [Offer(
            offer_id=offer_id, card_id=card_id, name=card_id, price_state="known",
            price=Money(amount_minor=100 if int(offer_id) >= self.first_match else 500, currency="RUB"),
            available=True, attrs=[], sale_unit="device", raw_params=[], source_refs=[],
            catalog_version="acceptance-fixture",
        )]

    def relations(self):
        return []


def paged_search(*, size=25, first_match=25, deadline_offset=5):
    events = []
    query = QueryState(goal="Синтетическая проверка расширения", mode="functional", filters=[
        Filter(filter_id="price", key="price_minor", op="lte", values=[100], strength="require",
               scope="topic", currency="RUB", source_turn_id="1", source_text="до одного рубля"),
    ])
    match = collect_matches(PagedOffers(size, first_match), query, None, monotonic() + deadline_offset,
                            lambda kind, payload: events.append((kind, payload)))
    return {
        "stages": [payload["stage"] for kind, payload in events if kind == "retrieval_completed"],
        "full": [item.candidate.card_id for item in match.primary if item.match_level == "full"],
        "partial_count": sum(item.match_level == "partial" for item in match.primary),
        "truncated": match.truncated,
    }


def test_unconfirmed_text_hits_stay_out_of_a_role_result():
    class Catalog(PagedOffers):
        def retrieve(self, query, cursor, limit):
            return CandidateBatch(
                candidates=[
                    Candidate(card_id="offer:30", channels=["role"], channel_ranks=[],
                              matched_role_ids=["server_hardware"], evidence=[], relevance="confirmed"),
                    Candidate(card_id="offer:1", channels=["fts"], channel_ranks=[],
                              matched_role_ids=[], evidence=[], relevance="hypothesis"),
                ],
                next_cursor=None, examined_card_count=2, exhausted=True, truncated=False,
            )

    query = QueryState(goal="серверы", mode="functional", role_ids=["server_hardware"], filters=[
        Filter(filter_id="price", key="price_minor", op="lte", values=[100], strength="require",
               scope="topic", currency="RUB", source_turn_id="1", source_text="до одного рубля"),
    ])
    match = collect_matches(Catalog(first_match=1), query, None, monotonic() + 5, lambda *_: None)
    assert [item.candidate.card_id for item in match.primary] == ["offer:30"]


def test_retrieval_expands_past_confirmed_partial_candidates():
    run = paged_search()
    assert run["stages"] == [24, 72]
    assert run["full"] == ["offer:25"]


def test_retrieval_reports_truncation_at_the_candidate_limit():
    run = paged_search(size=201, first_match=201)
    assert run["stages"] == [24, 72, 200]
    assert run["truncated"]
    assert not run["full"]


def test_expired_retrieval_deadline_reports_truncation():
    run = paged_search(deadline_offset=-1)
    assert run["truncated"]
    assert not run["full"]


if __name__ == "__main__":
    with TemporaryDirectory() as tmp:
        settings = replace(Settings.from_env(), catalog_path=CATALOG,
                           runtime_path=Path(tmp) / "state.sqlite", demo_mode=True,
                           external_llm_enabled=False)
        with TestClient(create_app(settings)) as api:
            print(json.dumps({
                "event_flow": event_flow(api),
                "A11_beyond_first_24": paged_search(),
                "A11_cap_200": paged_search(size=201, first_match=201),
                "A11_expired_deadline": paged_search(deadline_offset=-1),
            }, ensure_ascii=False, indent=2))

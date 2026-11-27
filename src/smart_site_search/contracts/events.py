from typing import Literal

from pydantic import AwareDatetime, Field

from .catalog import Contract

CatalogData = Literal["provided", "synthetic_fixture"]


class Provenance(Contract):
    catalog_data: CatalogData
    profile_mock: bool = False
    business_signals_mock: bool = False
    replay: bool = False


class EventProvenance(Provenance):
    behavior_mock: bool = False


class EventEnvelope(Contract):
    schema_version: Literal["1.0"] = "1.0"
    event_id: str
    event_type: str
    request_id: str | None = None
    session_id: str | None = None
    seq: int
    occurred_at: str
    received_at: str
    catalog_version: str | None = None
    app_version: str
    provenance: EventProvenance
    payload: dict


class LlmCallUsage(Contract):
    call_id: str
    provider_request_id: str | None = None
    provider: str
    model: str
    purpose: Literal["interpret", "expert_plan", "offline_enrichment"]
    attempt: int = Field(ge=1)
    status: Literal["success", "timeout", "provider_error", "invalid_output", "refused"]
    input_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    usage_source: Literal["provider", "unavailable"]
    pricing_version: str | None = None
    cost: str | None = None
    currency: str
    cost_status: Literal["calculated", "unknown"]
    latency_ms: int = Field(ge=0)
    error_code: str | None = None


class ClientEvent(Contract):
    event_id: str = Field(min_length=1, max_length=128)
    session_id: str
    request_id: str
    occurred_at: AwareDatetime
    event_type: Literal["results_impression", "result_click", "feedback", "mock_order"]
    payload: dict


class EventAccepted(Contract):
    event_id: str
    accepted: Literal[True] = True

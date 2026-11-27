from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from .catalog import CardId, Contract, Money, SourceRef
from .events import Provenance
from .intent import ContextDelta, Filter, Mode
from .search import BudgetStatus, Compatibility, ComponentResult, ConditionCheck

Group = Literal["primary_full", "primary_partial", "related"]


class SearchRequest(Contract):
    schema_version: Literal["1.0"]
    client_request_id: UUID
    session_id: UUID | None = None
    expected_revision: int = Field(ge=0)
    query: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    reference_request_id: UUID | None = None
    selected_card_id: CardId | None = None
    limit: int = Field(default=8, ge=4, le=12)

    @model_validator(mode="after")
    def check_request(self) -> Self:
        if not self.query.strip():
            raise ValueError("query must contain text")
        if self.session_id is None and self.expected_revision != 0:
            raise ValueError("new session starts at revision zero")
        if self.reference_request_id is not None and self.session_id is None:
            raise ValueError("reference requires a session")
        if self.selected_card_id and self.reference_request_id is None:
            raise ValueError("selected card requires a reference request")
        return self


class BudgetAlternative(Contract):
    offer_id: str
    name: str
    price: Money | None = None
    price_label: str
    available: bool | None = None
    url: str | None = None
    budget_status: BudgetStatus
    budget_difference: Money | None = None
    matched_conditions: list[ConditionCheck]
    unmet_conditions: list[ConditionCheck]
    reason: str


class ResultItem(Contract):
    card_id: CardId
    name: str
    vendor_names: list[str]
    kind: Literal["software", "hardware"]
    class_id: str
    image_url: str | None = None
    group: Group
    match_level: Literal["full", "partial"]
    component_ids: list[str]
    selected_offer_id: str
    price: Money | None = None
    quantity_total: Money | None = None
    price_label: str
    available: bool | None = None
    availability_label: str
    url: str | None = None
    url_kind: Literal["card", "offer", "none"]
    matched_conditions: list[ConditionCheck]
    unmet_conditions: list[ConditionCheck]
    reason: str
    caveats: list[str]
    source_refs: list[SourceRef]
    relation_id: str | None = None
    compatibility_status: Compatibility
    business_factors_mock: bool = False
    selected_offer_name: str | None = None
    budget_status: BudgetStatus | None = None
    budget_difference: Money | None = None
    budget_alternative: BudgetAlternative | None = None


class CompanionCard(Contract):
    card_id: CardId
    name: str
    vendor_names: list[str]
    kind: Literal["software", "hardware"]
    class_id: str
    image_url: str | None = None
    role_label: str
    url: str | None = None
    anchor_card_id: CardId


class Counts(Contract):
    primary_full: int = 0
    primary_partial: int = 0
    related: int = 0


class UsageSummary(Contract):
    llm_attempts: int = 0
    cache_hits: int = 0
    cost_known: str = "0"
    currency: str = "USD"
    cost_complete: bool = True
    latency_ms: int = 0


class Suggestion(Contract):
    label: str
    action: Literal["query", "remove_filter", "new_topic"]
    query: str | None = None
    filter_id: str | None = None


class SearchResponse(Contract):
    schema_version: Literal["1.0"] = "1.0"
    request_id: str
    client_request_id: str
    session_id: str
    revision: int
    catalog_version: str
    status: Literal["ok", "degraded", "needs_clarification"]
    mode: Mode
    interpreted_goal: str
    applied_filters: list[Filter] = Field(max_length=20)
    context_delta: ContextDelta
    items: list[ResultItem] = Field(max_length=12)
    companions: list[CompanionCard] = Field(default_factory=list, max_length=3)
    components: list[ComponentResult] = Field(max_length=4)
    expert_assumptions: list[str]
    primary_outcome: Literal["full", "partial", "none", "undetermined"]
    counts: Counts
    insufficient_relevant_items: bool
    search_truncated: bool
    catalog_absence_confirmed: bool = False
    warnings: list[str]
    suggestions: list[Suggestion]
    clarification_question: str | None = None
    trace_url: str
    usage: UsageSummary
    provenance: Provenance


class Health(Contract):
    status: Literal["alive"] = "alive"


class Readiness(Contract):
    ready: bool
    catalog_version: str | None
    storage_ok: bool
    llm_state: str


class ErrorDetail(Contract):
    code: str
    message: str
    retryable: bool


class ErrorResponse(Contract):
    schema_version: Literal["1.0"] = "1.0"
    request_id: str | None = None
    error: ErrorDetail
    session_id: str | None = None
    revision: int | None = None

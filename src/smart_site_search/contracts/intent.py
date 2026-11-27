from typing import Literal, Self

from pydantic import Field, model_validator

from .catalog import CardId, Contract, Scalar

Mode = Literal["direct", "functional", "expert"]
Scope = Literal["topic", "session", "component"]


class Filter(Contract):
    filter_id: str
    key: str
    op: Literal["eq", "in", "gte", "lte", "between"]
    values: list[Scalar] = Field(min_length=1, max_length=20)
    unit: str | None = None
    currency: str | None = None
    strength: Literal["require", "prefer", "exclude"]
    scope: Scope
    component_id: str | None = None
    source_turn_id: str
    source_text: str

    @model_validator(mode="after")
    def check_operator(self) -> Self:
        if self.op == "between":
            if (len(self.values) != 2 or any(type(v) is not int for v in self.values)
                    or self.values[0] > self.values[1]):
                raise ValueError("between requires two ordered integers")
        elif self.op != "in" and len(self.values) != 1:
            raise ValueError("operator requires one value")
        if (self.scope == "component") != (self.component_id is not None):
            raise ValueError("component_id must match component scope")
        return self


class FilterChange(Contract):
    action: Literal["set", "remove", "clear_scope"]
    scope: Scope
    component_id: str | None = None
    key: str | None = None
    filter: Filter | None = None

    @model_validator(mode="after")
    def check_action(self) -> Self:
        if (self.scope == "component") != (self.component_id is not None):
            raise ValueError("component_id must match component scope")
        if self.action == "set":
            if self.filter is None or self.filter.scope != self.scope or (
                self.filter.component_id != self.component_id
                or (self.key is not None and self.filter.key != self.key)
            ):
                raise ValueError("set requires a filter in the same scope")
        elif self.filter is not None or ((self.action == "remove") != bool(self.key)):
            raise ValueError("remove requires key; clear_scope takes only scope")
        return self


class Entity(Contract):
    text: str
    type: Literal["product", "vendor", "sku"]
    usage: Literal["target", "reference", "excluded"]
    resolved_id: str | None = None


class Reference(Contract):
    request_id: str
    card_id: CardId | None = None
    ordinal: int | None = Field(default=None, ge=1)


class Interpretation(Contract):
    mode: Mode
    operation: Literal["new_topic", "refine", "more", "reference"]
    goal: str
    entities: list[Entity] = Field(default_factory=list)
    class_ids: list[str] = Field(default_factory=list)
    role_ids: list[str] = Field(default_factory=list)
    search_terms: list[str] = Field(default_factory=list, max_length=12)
    filter_changes: list[FilterChange] = Field(default_factory=list, max_length=20)
    reference: Reference | None = None
    unresolved_constraints: list[str] = Field(default_factory=list)
    ambiguity: Literal["none", "needs_clarification"] = "none"
    clarification_question: str | None = None
    interpretation_source: Literal["rules", "llm", "cache", "degraded_rules"]


class QueryState(Contract):
    goal: str
    mode: Mode
    entities: list[Entity] = Field(default_factory=list)
    class_ids: list[str] = Field(default_factory=list)
    role_ids: list[str] = Field(default_factory=list)
    search_terms: list[str] = Field(default_factory=list, max_length=12)
    filters: list[Filter] = Field(default_factory=list, max_length=20)
    excluded_card_ids: list[CardId] = Field(default_factory=list)
    excluded_vendor_ids: list[str] = Field(default_factory=list)
    unresolved_constraints: list[str] = Field(default_factory=list)
    reference_card_id: CardId | None = None
    omit_seen: bool = False


class ContextDelta(Contract):
    added_filter_ids: list[str] = Field(default_factory=list)
    replaced_filter_ids: list[str] = Field(default_factory=list)
    removed_filter_ids: list[str] = Field(default_factory=list)
    resolved_reference: CardId | None = None
    topic_changed: bool = False


class ExpertComponent(Contract):
    component_id: str
    label: str
    role_ids: list[str]
    required: bool
    search_terms: list[str] = Field(max_length=12)
    filters: list[Filter] = Field(max_length=20)
    rationale: str
    unknowns: list[str]


class ExpertPlan(Contract):
    plan_id: str
    goal: str
    scope_limited: bool
    assumptions: list[str]
    clarification_question: str | None = None
    components: list[ExpertComponent] = Field(min_length=1, max_length=4)
    knowledge_source: Literal["external_llm", "cached_external_llm"]
    model_call_id: str


class RecentResult(Contract):
    request_id: str
    display_order: list[CardId] = Field(max_length=12)


class LastTurn(Contract):
    query: str
    response_summary: str


class Profile(Contract):
    segment: str | None = None
    is_mock: bool


class SearchSession(Contract):
    session_id: str
    revision: int
    topic_id: str
    goal: str | None = None
    mode: Mode | None = None
    catalog_version: str
    taxonomy_version: str
    query_state: QueryState | None = None
    recent_results: list[RecentResult] = Field(default_factory=list)
    seen_card_ids: list[CardId] = Field(default_factory=list)
    expert_plan: ExpertPlan | None = None
    last_turns: list[LastTurn] = Field(default_factory=list)
    profile: Profile | None = None
    updated_at: str
    expires_at: str

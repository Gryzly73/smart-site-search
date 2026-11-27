"""Provider schemas contain no catalog facts, server identifiers or telemetry."""

from typing import Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr

from smart_site_search.contracts.catalog import Contract
from smart_site_search.contracts.intent import Mode


class ModelEntity(Contract):
    text: str
    type: Literal["product", "sku", "vendor"]
    usage: Literal["target", "reference", "excluded"]


class ModelFilter(Contract):
    key: str
    op: Literal["eq", "in", "gte", "lte", "between"]
    values: list[StrictStr | StrictInt | StrictBool] = Field(min_length=1, max_length=20)
    unit: str | None
    currency: str | None
    strength: Literal["require", "prefer", "exclude"]
    source_text: str


class ModelChange(Contract):
    action: Literal["set", "remove", "clear_scope"]
    scope: Literal["topic", "session"]
    key: str | None
    filter: ModelFilter | None


class ModelIntent(Contract):
    mode: Mode
    operation: Literal["new_topic", "refine", "more", "reference"]
    goal: str
    entities: list[ModelEntity] = Field(max_length=12)
    class_ids: list[str] = Field(max_length=12)
    role_ids: list[str] = Field(max_length=12)
    search_terms: list[str] = Field(max_length=12)
    filter_changes: list[ModelChange] = Field(max_length=20)
    reference_ordinal: int | None
    unresolved_constraints: list[str] = Field(max_length=20)
    clarification_question: str | None


class ModelComponent(Contract):
    label: str
    role_ids: list[str] = Field(min_length=1, max_length=8)
    required: bool
    search_terms: list[str] = Field(max_length=12)
    filters: list[ModelFilter] = Field(max_length=20)
    rationale: str
    unknowns: list[str] = Field(max_length=12)


class ModelPlan(Contract):
    scope_limited: bool
    assumptions: list[str] = Field(max_length=12)
    clarification_question: str | None
    components: list[ModelComponent] = Field(min_length=1, max_length=4)

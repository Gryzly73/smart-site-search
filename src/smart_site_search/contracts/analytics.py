from typing import Literal

from pydantic import Field

from .api import Group
from .catalog import Contract, Money
from .search import BudgetStatus

DialogueOutcome = Literal["full", "partial", "none", "undetermined", "error", "pending"]
DialogueMode = Literal["direct", "functional", "expert", "unknown"]
DialogueModeFilter = Literal["all", "direct", "functional", "expert"]


class Metric(Contract):
    value: float | None
    numerator: int | None
    denominator: int | None
    availability: Literal["measured", "mock", "unavailable", "scenario"]
    definition: str
    limitations: list[str]


class ReportMetric(Contract):
    metric_id: str
    metric: Metric


class Demand(Contract):
    goal_or_role: str
    requests: int
    sessions: int


class Gap(Contract):
    code: str
    count: int
    evidence_status: str
    recommendation: str


class ModeCost(Contract):
    mode: str
    requests: int
    calls: int
    known_cost: str
    unknown_calls: int
    currency: str


class DialogueBudget(Contract):
    key: Literal["price_minor", "total_price_minor"]
    op: Literal["lte", "between"]
    values: list[int]
    currency: str


class DialogueOffer(Contract):
    card_name: str
    offer_name: str | None = None
    group: Group
    price: Money | None = None
    budget_status: BudgetStatus | None = None
    budget_difference: Money | None = None
    alternative: bool
    clicked: bool


class DialogueFilterChanges(Contract):
    added: list[str]
    replaced: list[str]
    removed: list[str]


class DialogueTurn(Contract):
    started_at: str
    mode: DialogueMode
    outcome: DialogueOutcome
    result_kind: Literal["exact", "price_near", "characteristic_near", "unavailable",
                         "clarification", "partial", "other"] = "other"
    request_text: str = ""
    answer_text: str = ""
    clarification: bool
    filter_keys: list[str]
    filter_changes: DialogueFilterChanges
    budgets: list[DialogueBudget]
    offers: list[DialogueOffer]
    impression_recorded: bool
    feedback: Literal["useful", "not_useful"] | None = None
    issues: list[str]


class DialogueExample(Contract):
    topic_id: str
    started_at: str
    result_kind: Literal["exact", "price_near", "characteristic_near", "unavailable",
                         "clarification", "partial", "other"]
    request_text: str = ""
    answer_text: str = ""
    product_name: str | None = None


class DialogueSummary(Contract):
    kind: Literal["exact", "price_near", "characteristic_near", "unavailable",
                  "clarification", "partial", "other"]
    count: int


class DialogueTopic(Contract):
    topic_id: str
    started_at: str
    mode: DialogueMode
    result_kind: Literal["exact", "price_near", "characteristic_near", "unavailable",
                         "clarification", "partial", "other"] = "other"
    role_ids: list[str]
    class_ids: list[str]
    first_outcome: DialogueOutcome
    last_outcome: DialogueOutcome
    observing: bool
    recovery: Literal["preserved", "changed"] | None = None
    issues: list[str]
    turns: list[DialogueTurn]


class DialogueTrend(Contract):
    date: str
    topics: int
    first_full: int
    recovered: int


class DialogueProblem(Contract):
    code: str
    count: int
    recommendation: str


class DialogueReport(Contract):
    observation_minutes: int
    unattributed_requests: int
    topics_total: int
    filtered_total: int
    offset: int
    limit: int
    metrics: list[ReportMetric]
    trend: list[DialogueTrend]
    problems: list[DialogueProblem]
    summary: list[DialogueSummary] = Field(default_factory=list)
    examples: list[DialogueExample] = Field(default_factory=list)
    topics: list[DialogueTopic]


class Cohort(Contract):
    name: Literal["real", "mock"]
    metrics: list[ReportMetric]
    demand: list[Demand]
    gaps: list[Gap]
    costs_by_mode: list[ModeCost]
    dialogues: DialogueReport | None = None


class AnalyticsReport(Contract):
    schema_version: Literal["1.0"] = "1.0"
    from_: str = Field(alias="from")
    to: str
    generated_at: str
    cohorts: list[Cohort]

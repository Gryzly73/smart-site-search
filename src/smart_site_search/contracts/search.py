from typing import Literal

from pydantic import Field

from .catalog import CardId, Contract, Money, Scalar, SourceRef

Match = Literal["full", "partial", "rejected"]
Compatibility = Literal["not_verified", "evidence_available"]
Channel = Literal["exact", "alias", "fts", "fuzzy", "role"]
BudgetStatus = Literal["within", "over", "unconfirmed"]


class ChannelRank(Contract):
    channel: Channel
    rank: int


class Candidate(Contract):
    card_id: CardId
    component_id: str | None = None
    channels: list[Channel]
    channel_ranks: list[ChannelRank]
    lexical_score: float | None = None
    fuzzy_score: float | None = None
    matched_role_ids: list[str]
    evidence: list[SourceRef]
    relevance: Literal["confirmed", "hypothesis", "insufficient"]


class CandidateBatch(Contract):
    candidates: list[Candidate] = Field(max_length=200)
    next_cursor: str | None = None
    examined_card_count: int
    exhausted: bool
    truncated: bool
    truncation_reason: Literal["candidate_limit", "deadline"] | None = None


class ConditionCheck(Contract):
    filter_id: str
    status: Literal["match", "mismatch", "unknown"]
    reason_code: Literal["value_match", "value_mismatch", "missing_attribute",
                         "conflicting_values", "unknown_price",
                         "unknown_quantity_basis", "unsupported_condition"]
    actual_values: list[Scalar]
    source_refs: list[SourceRef]


class OfferCheck(Contract):
    offer_id: str
    card_id: CardId
    conditions: list[ConditionCheck]
    required_match: Match
    price: Money | None = None
    quantity_total: Money | None = None
    quantity_basis: str | None = None
    available: bool | None = None
    url: str | None = None
    source_refs: list[SourceRef]


class RankFactors(Contract):
    relevance_tier: int
    identity_rank: int
    preference_matches: int
    availability_rank: int
    price_minor: int | None = None
    popularity: float | None = None
    margin: float | None = None
    business_factors_mock: bool = False


class VerifiedCandidate(Contract):
    candidate: Candidate
    match_level: Match
    selected_offer: OfferCheck | None = None
    alternate_offers: list[OfferCheck]
    checks_complete_for_card: bool
    reason_codes: list[str]
    rank_factors: RankFactors


class ComponentResult(Contract):
    component_id: str
    label: str | None = None
    rationale: str | None = None
    unknowns: list[str] = Field(default_factory=list)
    status: Literal["covered", "partial", "missing"]
    full_card_ids: list[CardId]
    partial_card_ids: list[CardId]
    missing_reason: str | None = None
    compatibility_status: Compatibility
    compatibility_evidence: list[SourceRef]


class RelatedResult(Contract):
    verified: VerifiedCandidate
    relation_id: str
    anchor_card_id: CardId | None = None
    anchor_role_id: str | None = None
    reason: str
    compatibility_status: Compatibility
    compatibility_evidence: list[SourceRef]

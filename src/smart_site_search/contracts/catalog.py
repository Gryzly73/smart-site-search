from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

CardId = Annotated[str, Field(pattern=r"^(group|offer):.+$")]
Scalar = StrictStr | StrictInt | StrictBool


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRef(Contract):
    source: Literal["feed", "category", "taxonomy_rule", "reviewed_enrichment",
                    "external_doc", "llm_hypothesis", "mock"]
    catalog_version: str | None = None
    entity_id: str
    field: str
    raw_index: int | None = None
    artifact_version: str | None = None
    note: str | None = None
    document_url: str | None = None
    checked_at: str | None = None


class Money(Contract):
    amount_minor: int = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class Attribute(Contract):
    key: str
    knowledge: Literal["known", "unknown", "conflicting"]
    values: list[Scalar]
    unit: str | None = None
    min_value: int | None = None
    max_value: int | None = None
    source_refs: list[SourceRef]


class Role(Contract):
    role_id: str
    source_refs: list[SourceRef]
    verified: bool


class Card(Contract):
    card_id: CardId
    kind: Literal["software", "hardware"]
    name: str
    vendor_ids: list[str]
    class_id: str
    roles: list[Role]
    shelf_ids: list[str]
    description: str | None = None
    image_url: str | None = None
    card_url: str | None = None
    card_url_source: SourceRef | None = None
    offer_count: int
    available_count: int
    aggregate_price_min: Money | None = None
    aggregate_price_max: Money | None = None
    facet_values: list[Attribute]
    catalog_version: str


class RawParam(Contract):
    name: str
    value: str
    index: int


class Offer(Contract):
    offer_id: str
    card_id: CardId
    name: str
    vendor_code: str | None = None
    available: bool | None = None
    price: Money | None = None
    price_state: Literal["known", "on_request", "unknown"]
    url: str | None = None
    image_url: str | None = None
    attrs: list[Attribute]
    sales_notes_raw: str | None = None
    sale_unit: Literal["license", "device", "pack", "unknown"]
    pack_size: int | None = None
    min_order_quantity: int | None = None
    max_order_quantity: int | None = None
    raw_params: list[RawParam]
    source_refs: list[SourceRef]
    catalog_version: str


class RelationEndpoint(Contract):
    type: Literal["class", "role"]
    id: str


class Relation(Contract):
    relation_id: str
    source: RelationEndpoint
    target: RelationEndpoint
    relation_type: Literal["complements", "supports"]
    reason: str
    propagated_filter_keys: list[str]
    source_refs: list[SourceRef]
    version: str

from datetime import UTC
from typing import Literal

from pydantic import Field, ValidationError

from ..contracts.api import Group, SearchResponse
from ..contracts.catalog import CardId, Contract, Money
from ..contracts.events import ClientEvent, EventAccepted, EventEnvelope
from ..contracts.intent import SearchSession
from .store import ApiError, RuntimeStore


class Position(Contract):
    card_id: CardId
    group: Group
    position: int = Field(ge=1, le=12, strict=True)


class Click(Position):
    offer_id: str
    request_id: str | None = None


class Impression(Contract):
    request_id: str
    visible_items: list[Position] = Field(max_length=12)


class Feedback(Contract):
    rating: Literal["useful", "not_useful"]
    reason: str | None = Field(default=None, max_length=500)
    request_id: str | None = None


class MockOrder(Contract):
    order_id: str = Field(min_length=1, max_length=128)
    attributed_request_id: str
    card_ids: list[CardId] = Field(min_length=1, max_length=12)
    total: Money | None = None
    attribution_method: str = Field(min_length=1, max_length=64)


MODELS = {
    "results_impression": Impression,
    "result_click": Click,
    "feedback": Feedback,
    "mock_order": MockOrder,
}


def accept_event(store: RuntimeStore, event: ClientEvent, demo_mode: bool) -> EventAccepted:
    if event.event_type == "mock_order" and not demo_mode:
        raise ApiError(422, "mock_order_disabled", "Синтетические заказы отключены.")
    try:
        payload = MODELS[event.event_type].model_validate(event.payload)
    except ValidationError as exc:
        raise ApiError(422, "invalid_event_payload", "Проверьте данные события.") from exc

    with store.connection(write=True) as db:
        row = db.execute("""
            SELECT r.response_json, s.state_json FROM requests r JOIN sessions s USING (session_id)
            WHERE r.request_id=? AND r.session_id=? AND r.status='complete'
        """, (event.request_id, event.session_id)).fetchone()
        if row is None:
            raise ApiError(422, "invalid_event_request", "Запрос отсутствует в этой сессии.")
        response = SearchResponse.model_validate_json(row["response_json"])
        _validate_attribution(payload, response)
        excluded_fields = {"reason", "request_id"} if isinstance(payload, Feedback) else (
            {"request_id"} if isinstance(payload, Click) else None)
        values = payload.model_dump(mode="json", exclude=excluded_fields)
        prior = db.execute("SELECT envelope_json FROM events WHERE event_id=?", (event.event_id,)).fetchone()
        if prior:
            old = EventEnvelope.model_validate_json(prior["envelope_json"])
            if (old.request_id, old.session_id, old.event_type, old.payload) == (
                event.request_id, event.session_id, event.event_type, values
            ):
                return EventAccepted(event_id=event.event_id)
            raise ApiError(409, "event_id_conflict", "Идентификатор события уже используется.")
        state = SearchSession.model_validate_json(row["state_json"]).model_copy(
            update={"catalog_version": response.catalog_version}
        )
        store._append(db, event.request_id, state, event.event_type, values,
                      event_id=event.event_id, occurred_at=event.occurred_at.astimezone(UTC).isoformat(),
                      behavior_mock=isinstance(payload, MockOrder))
    return EventAccepted(event_id=event.event_id)


def _validate_attribution(payload, response):
    groups = {"primary_full": 0, "primary_partial": 1, "related": 2}
    ordered = sorted(response.items, key=lambda item: groups[item.group])
    by_id = {item.card_id: (position, item) for position, item in enumerate(ordered, 1)}
    positions = []
    valid = True
    if isinstance(payload, Impression):
        ids = {p.card_id for p in payload.visible_items}
        valid = payload.request_id == response.request_id and len(ids) == len(payload.visible_items)
        positions = payload.visible_items
    elif isinstance(payload, Click):
        valid = payload.request_id in (None, response.request_id)
        positions = [payload]
    elif isinstance(payload, Feedback):
        valid = payload.request_id in (None, response.request_id)
    elif isinstance(payload, MockOrder):
        valid = (payload.attributed_request_id == response.request_id
                 and set(payload.card_ids) <= by_id.keys())
    for entry in positions:
        found = by_id.get(entry.card_id)
        valid &= bool(found and found[0] == entry.position and found[1].group == entry.group)
        if found and isinstance(entry, Click):
            item = found[1]
            valid &= entry.offer_id in {item.selected_offer_id,
                                       item.budget_alternative.offer_id if item.budget_alternative else None}
    if not valid:
        raise ApiError(422, "invalid_event_attribution", "Событие не соответствует сохранённой выдаче.")

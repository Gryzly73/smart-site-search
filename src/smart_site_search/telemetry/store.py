import hashlib
import logging
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from ..contracts.api import SearchRequest, SearchResponse, UsageSummary
from ..contracts.events import EventEnvelope, EventProvenance, LlmCallUsage
from ..contracts.intent import SearchSession

logger = logging.getLogger("uvicorn.error")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, *,
                 request_id: str | None = None, session_id: str | None = None,
                 revision: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.code = code
        self.request_id = request_id
        self.session_id = session_id
        self.revision = revision
        self.retryable = retryable


class RuntimeStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection(write=True) as db:
            db.executescript(Path(__file__).resolve().parents[1].joinpath(
                "runtime_schema.sql"
            ).read_text(encoding="utf-8"))
            rows = db.execute("""
                SELECT r.request_id, s.state_json
                FROM requests r JOIN sessions s USING (session_id)
                WHERE r.status='running'
            """).fetchall()
            for row in rows:
                state = SearchSession.model_validate_json(row["state_json"])
                self._append(db, row["request_id"], state, "request_interrupted", {
                    "last_stage": "request", "reason": "restart",
                    "known_call_ids": [c[0] for c in db.execute(
                        "SELECT call_id FROM llm_calls WHERE request_id=?", (row["request_id"],)
                    )],
                })
            db.execute("UPDATE requests SET status='interrupted' WHERE status='running'")

    @contextmanager
    def connection(self, *, write: bool = False):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def storage_ok(self) -> bool:
        try:
            with self.connection(write=True) as db:
                db.execute("UPDATE sessions SET revision=revision WHERE 0")
        except sqlite3.Error:
            return False
        return True

    def start(self, req: SearchRequest, catalog_version: str, taxonomy_version: str):
        token = str(req.client_request_id)
        digest = hashlib.sha256(req.model_dump_json().encode()).hexdigest()
        session_id = str(req.session_id) if req.session_id is not None else None
        with self.connection(write=True) as db:
            if session_id is None:
                prior = db.execute("""
                    SELECT * FROM requests WHERE client_request_id=? AND creates_session=1
                """, (token,)).fetchone()
            else:
                prior = db.execute("""
                    SELECT * FROM requests WHERE session_id=? AND client_request_id=?
                """, (session_id, token)).fetchone()
            if prior:
                error_data = {"request_id": prior["request_id"], "session_id": prior["session_id"]}
                if digest != prior["payload_hash"]:
                    raise ApiError(409, "idempotency_conflict", "Идентификатор уже занят другим запросом.",
                                   **error_data)
                if prior["status"] == "complete":
                    return SearchResponse.model_validate_json(prior["response_json"])
                code = "session_busy" if prior["status"] == "running" else "request_interrupted"
                raise ApiError(409, code, "Повторите после завершения или начните новый запрос.",
                               retryable=True, **error_data)

            now = datetime.now(UTC)
            if session_id is None:
                state = SearchSession(
                    session_id=str(uuid4()), revision=0, topic_id=str(uuid4()),
                    catalog_version=catalog_version, taxonomy_version=taxonomy_version,
                    updated_at=now.isoformat(), expires_at=(now + timedelta(hours=12)).isoformat(),
                )
                db.execute("INSERT INTO sessions VALUES (?,?,?,?)", (
                    state.session_id, 0, state.expires_at, state.model_dump_json()
                ))
            else:
                row = db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
                if row is None or datetime.fromisoformat(row["expires_at"]) <= now:
                    raise ApiError(410, "session_expired", "Начните новый поиск.", session_id=session_id)
                state = SearchSession.model_validate_json(row["state_json"])
                if db.execute(
                    "SELECT 1 FROM requests WHERE session_id=? AND status='running'", (session_id,)
                ).fetchone():
                    raise ApiError(409, "session_busy", "Предыдущий запрос ещё выполняется.",
                                   session_id=session_id, revision=state.revision, retryable=True)
                if state.revision != req.expected_revision:
                    raise ApiError(409, "revision_conflict", "Поисковый диалог уже обновился.",
                                   session_id=session_id, revision=state.revision)
                if req.reference_request_id and not any(
                    item.request_id == str(req.reference_request_id)
                    and (req.selected_card_id is None or req.selected_card_id in item.display_order)
                    for item in state.recent_results
                ):
                    raise ApiError(422, "invalid_reference", "Карточка отсутствует в указанном ответе.",
                                   session_id=session_id, revision=state.revision)

            request_id = str(uuid4())
            db.execute("""
                INSERT INTO requests(request_id,session_id,client_request_id,creates_session,
                                     payload_hash,status,started_at) VALUES (?,?,?,?,?,'running',?)
            """, (request_id, state.session_id, token, int(session_id is None), digest, now.isoformat()))
            self._append(db, request_id, state, "request_received", {
                "client_request_id": token,
                "expected_revision": req.expected_revision,
                "query_redacted": "[скрыто]",
                "normalized_query": None,
                "request_kind": "continuation" if session_id else "new_session",
            })
            return request_id, state

    def emit(self, request_id: str, state: SearchSession, kind: str, payload: dict) -> None:
        if kind == "llm_call_completed":
            payload = LlmCallUsage.model_validate(payload).model_dump(mode="json")
        with self.connection(write=True) as db:
            event = self._append(db, request_id, state, kind, payload)
            if kind == "llm_call_started":
                db.execute("INSERT INTO llm_calls VALUES (?,?,?,NULL)", (
                    payload["call_id"], request_id, event.model_dump_json()
                ))
            elif kind == "llm_call_completed":
                updated = db.execute("""
                    UPDATE llm_calls SET usage_json=?
                    WHERE call_id=? AND request_id=? AND usage_json IS NULL
                """, (event.model_dump_json(), payload["call_id"], request_id))
                if updated.rowcount != 1:
                    raise ValueError("llm_call_completed requires a matching call start")
        logger.info("%s", event.model_dump_json())

    def _append(self, db, request_id: str, state: SearchSession, kind: str, payload: dict,
                *, event_id: str | None = None, occurred_at: str | None = None,
                behavior_mock: bool = False) -> EventEnvelope:
        seq = db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE request_id=?",
                         (request_id,)).fetchone()[0]
        now = utc_now()
        event = EventEnvelope(
            event_id=event_id or str(uuid4()), event_type=kind, request_id=request_id,
            session_id=state.session_id, catalog_version=state.catalog_version,
            seq=seq, occurred_at=occurred_at or now, received_at=now, app_version="0.1.0",
            provenance=EventProvenance(
                catalog_data="provided", behavior_mock=behavior_mock,
                profile_mock=bool(state.profile and state.profile.is_mock),
            ),
            payload=payload,
        )
        db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)", (
            event.event_id, request_id, state.session_id, seq, kind,
            event.occurred_at, event.model_dump_json()
        ))
        return event

    def finish(self, response: SearchResponse, state: SearchSession) -> None:
        with self.connection(write=True) as db:
            updated = db.execute("""
                UPDATE sessions SET revision=?, expires_at=?, state_json=?
                WHERE session_id=? AND revision=?
            """, (state.revision, state.expires_at, state.model_dump_json(),
                  state.session_id, state.revision - 1))
            if updated.rowcount != 1:
                raise ApiError(409, "revision_conflict", "Поисковый диалог уже обновился.")
            db.execute("""
                UPDATE requests SET status='complete', completed_at=?, mode=?, response_json=?
                WHERE request_id=?
            """, (state.updated_at, response.mode, response.model_dump_json(), response.request_id))
            payload = {
                "mode": response.mode, "primary_outcome": response.primary_outcome,
                "counts": response.counts.model_dump(),
                "displayed_card_ids": [item.card_id for item in response.items],
                "displayed_offer_ids": [item.selected_offer_id for item in response.items],
                "component_statuses": {c.component_id: c.status for c in response.components},
                "search_truncated": response.search_truncated,
                "insufficient_relevant_items": response.insufficient_relevant_items,
                "latency_ms": response.usage.latency_ms, "cost_known": response.usage.cost_known,
                "cost_complete": response.usage.cost_complete,
            }
            event = self._append(db, response.request_id, state, "response_completed", payload)
        logger.info("%s", event.model_dump_json())

    def fail(self, request_id: str, state: SearchSession) -> None:
        with self.connection(write=True) as db:
            db.execute("UPDATE requests SET status='failed',completed_at=? WHERE request_id=?",
                       (utc_now(), request_id))
            event = self._append(db, request_id, state, "technical_error", {
                "stage": "search", "code": "search_failure", "retryable": True,
                "duration_ms": None, "sanitized_message": "Search processing failed",
            })
        logger.info("%s", event.model_dump_json())

    def trace(self, request_id: str) -> list[EventEnvelope]:
        with self.connection() as db:
            rows = db.execute("""
                SELECT envelope_json FROM events WHERE request_id=? ORDER BY seq
            """, (request_id,)).fetchall()
        if not rows:
            raise ApiError(404, "unknown_request", "Запрос не найден.")
        return [EventEnvelope.model_validate_json(row[0]) for row in rows]

    def usage(self, request_id: str, latency_ms: int) -> UsageSummary:
        with self.connection() as db:
            calls = db.execute("SELECT usage_json FROM llm_calls WHERE request_id=?",
                               (request_id,)).fetchall()
            cache_hits = db.execute("""
                SELECT COUNT(*) FROM events WHERE request_id=? AND event_type='llm_cache_hit'
            """, (request_id,)).fetchone()[0]
        values = [
            LlmCallUsage.model_validate(EventEnvelope.model_validate_json(row[0]).payload)
            if row[0] else None
            for row in calls
        ]
        known = [Decimal(usage.cost) for usage in values if usage and usage.cost is not None]
        currency = next((usage.currency for usage in values if usage), "USD")
        return UsageSummary(
            llm_attempts=len(calls), cache_hits=cache_hits,
            cost_known=format(sum(known, Decimal(0)), "f"), currency=currency,
            cost_complete=len(known) == len(calls), latency_ms=latency_ms,
        )

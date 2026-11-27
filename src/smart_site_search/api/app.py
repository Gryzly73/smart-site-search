import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AwareDatetime

from ..contracts.analytics import AnalyticsReport, DialogueModeFilter
from ..contracts.api import ErrorDetail, ErrorResponse, Health, Readiness, SearchRequest, SearchResponse
from ..contracts.events import ClientEvent, EventAccepted, EventEnvelope
from ..settings import Settings
from ..telemetry import ApiError, RuntimeStore

STATIC = Path(__file__).resolve().parents[1] / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        app.state.store = RuntimeStore(settings.runtime_path)
        yield

    app = FastAPI(title="Умный поиск на сайте", version="1.0", lifespan=lifespan,
                  responses={422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})

    @app.exception_handler(ApiError)
    async def application_error(_, exc: ApiError):
        body = ErrorResponse(
            request_id=exc.request_id, session_id=exc.session_id, revision=exc.revision,
            error=ErrorDetail(code=exc.code, message=str(exc), retryable=exc.retryable),
        )
        return JSONResponse(status_code=exc.status, content=body.model_dump(mode="json"))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, _):
        return await application_error(request, ApiError(
            422, "invalid_request", "Проверьте поля запроса."
        ))

    @app.exception_handler(sqlite3.Error)
    async def database_error(request, _):
        return await application_error(request, ApiError(
            503, "storage_unavailable", "Хранилище временно недоступно.", retryable=True,
        ))

    @app.get("/", include_in_schema=False)
    def home():
        index = STATIC / "index.html"
        if index.is_file():
            return FileResponse(index)
        return HTMLResponse('<meta charset="utf-8"><title>Умный поиск</title>'
                            '<p>Интерфейс поиска готовится. <a href="/docs">Открыть API</a></p>')

    @app.get("/widget", include_in_schema=False)
    def widget():
        page = STATIC / "widget.html"
        if page.is_file():
            return FileResponse(page)
        return HTMLResponse('<meta charset="utf-8"><title>Виджет</title><p>Страница виджета готовится.</p>')

    @app.get("/analytics", include_in_schema=False)
    def dashboard():
        dashboard_page = STATIC / "analytics.html"
        if dashboard_page.is_file():
            return FileResponse(dashboard_page)
        return HTMLResponse('<meta charset="utf-8"><title>Аналитика поиска</title>'
                            '<a href="/docs#/default/analytics_api_v1_analytics_get">API аналитики</a>')

    if STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/healthz")
    def health() -> Health:
        return Health()

    @app.get("/readyz")
    def ready() -> Readiness:
        from ..service import catalog_readiness

        version = catalog_readiness(settings.catalog_path)
        storage_ok = app.state.store.storage_ok()
        return Readiness(ready=bool(version and storage_ok), catalog_version=version,
                         storage_ok=storage_ok, llm_state=settings.llm_state)

    @app.post("/api/v1/search", responses={409: {"model": ErrorResponse}, 410: {"model": ErrorResponse}})
    def search(req: SearchRequest) -> SearchResponse:
        from ..service import run_search

        return run_search(req, settings, app.state.store)

    @app.get("/api/v1/trace/{request_id}", responses={404: {"model": ErrorResponse}})
    def trace(request_id: UUID) -> list[EventEnvelope]:
        return app.state.store.trace(str(request_id))

    @app.post("/api/v1/events", status_code=202)
    def events(event: ClientEvent) -> EventAccepted:
        from ..telemetry.client import accept_event

        return accept_event(app.state.store, event, demo_mode=settings.demo_mode)

    @app.get("/api/v1/analytics")
    def analytics(
        start: AwareDatetime = Query(alias="from"),
        end: AwareDatetime = Query(alias="to"),
        cohort: Literal["real", "mock", "all"] = "all",
        mode: DialogueModeFilter = "all",
        issue: str | None = Query(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$"),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=25, ge=1, le=50),
    ) -> AnalyticsReport:
        from ..analytics import build_report

        if start >= end:
            raise ApiError(422, "invalid_range", "Начало периода должно предшествовать концу.")
        return build_report(app.state.store, datetime.astimezone(start, UTC),
                            datetime.astimezone(end, UTC), cohort,
                            mode=mode, issue=issue, offset=offset, limit=limit)

    return app


app = create_app()

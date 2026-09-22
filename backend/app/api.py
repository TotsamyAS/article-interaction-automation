import sqlite3
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi import Query as QueryParameter
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response

from .config import Settings, load_settings
from .analytics import AnalyticsFilter, AnalyticsService, csv_bundle, excel_workbook, table_csv
from .contracts import (AttemptInput, AttemptView, EventBatch, M3AttemptInput, Mode,
                        Query, QueryResult, SessionCreate, SessionView, TaskRecord, TrialView)
from .database import Database, encode
from .errors import DomainError
from .service import ExperimentService, now_ms
from .access import (AccessService, COOKIE_NAME, Principal, Signatures,
                     key_from_runtime as invitation_key_from_runtime, require_principal, same_origin)
from .manual_query import ManualTags, builders, compile_tags, suggestions
from .engine import execute
from .llm import OpenAICompatibleInterpreter, key_from_runtime as llm_key_from_runtime


def create_app(settings: Settings | None = None, clock=now_ms, signatures_factory=None, m3_interpreter_factory=None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app):
        signatures = signatures_factory() if signatures_factory is not None else Signatures(invitation_key_from_runtime())
        database = Database(settings)
        database.initialize()
        service = ExperimentService(database, clock)
        if m3_interpreter_factory is None:
            interpreter = OpenAICompatibleInterpreter(
                base_url=settings.llm_base_url,
                api_key=llm_key_from_runtime(),
                model=settings.llm_model,
                temperature=settings.llm_temperature,
                timeout_seconds=settings.llm_timeout_seconds,
                records=service.records,
                reference_date=settings.reference_date,
            )
        else:
            interpreter = m3_interpreter_factory(settings, service.records)
        service.configure_m3(interpreter)
        app.state.service = service
        app.state.access = AccessService(database, signatures, clock=lambda: clock() / 1000)
        yield

    app = FastAPI(title="Экспериментальный стенд — API", version="0.1.0", lifespan=lifespan)
    router = APIRouter(prefix="/api", dependencies=[Depends(require_principal), Depends(same_origin)])

    @app.middleware("http")
    async def private_responses(request: Request, call_next):
        response = await call_next(request)
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/access/enter", include_in_schema=False)
    def enter(invitation: str):
        principal = app.state.access.validate(invitation, "invite")
        response = RedirectResponse(settings.login_redirect_path, status_code=303)
        response.set_cookie(COOKIE_NAME, app.state.access.cookie(principal),
                            max_age=settings.access_cookie_seconds, httponly=True,
                            secure=settings.public_base_url.startswith("https://"), samesite="lax", path="/")
        return response

    @router.post("/access/logout", tags=["Доступ"])
    def logout():
        response = Response(status_code=204)
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @router.get("/me", tags=["Доступ"])
    def me(principal: Annotated[Principal, Depends(require_principal)]):
        with app.state.service.database.transaction() as connection:
            sessions = [dict(row) for row in connection.execute(
                "SELECT id, kind FROM sessions WHERE participant_code = ? ORDER BY created_ms, id", (principal.code,))]
        return {"participant_code": principal.code, "role": principal.role,
                "sessions": [app.state.service.session(row["id"]) for row in sessions]}

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, error: DomainError):
        return JSONResponse(status_code=error.status, content={"error": {"code": error.code, "message": error.message}})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        # Do not echo arbitrary request values into responses or logs.
        return JSONResponse(status_code=422, content={"error": {"code": "invalid_request", "message": "Проверьте структуру запроса.",
                            "fields": [{"path": list(item["loc"]), "type": item["type"]} for item in error.errors()]}})

    @app.exception_handler(sqlite3.OperationalError)
    async def database_error(request: Request, error: sqlite3.OperationalError):
        return JSONResponse(status_code=503, content={"error": {"code": "storage_unavailable", "message": "Хранилище временно недоступно. Повторите запрос с тем же request_id."}})

    @app.get("/health", tags=["Состояние"])
    def health():
        with app.state.service.database.transaction() as connection:
            connection.execute("SELECT 1").fetchone()
        return {"status": "ok"}

    @router.get("/protocol", tags=["Протокол"])
    def protocol():
        return {"manifest": app.state.service.manifest,
                "modes": [{"id": mode.value, "available": mode in app.state.service.available_modes} for mode in Mode],
                "frontend_ready": True}

    @router.get("/records", response_model=list[TaskRecord], tags=["Данные"])
    def records():
        return app.state.service.records

    @router.get("/manual-query", tags=["Ручной ввод"])
    def manual_query_help():
        return {"placeholder": "Выберите или введите поле, например Статус",
                "instruction": "Вся сборка запроса — в одной строке. Введите поле и значение, подтверждая Enter или Tab. После готового условия можно ввести «ИЛИ» и добавить значение того же поля, либо «И» и начать другое условие. Группировка, Итог, Экстремум и Экспорт вводятся здесь же. В конце нажмите «Выполнить».",
                "examples": ["Статус: В работе ИЛИ На ревью", "Приоритет != Низкий", "Группировка: эпик", "Итог: количество", "Экстремум: максимум", "Экспорт: CSV"],
                "suggestions": suggestions(app.state.service.records), "builders": builders(app.state.service.records)}

    @router.post("/manual-query/compile", response_model=Query, tags=["Ручной ввод"])
    def manual_query_compile(body: ManualTags):
        service = app.state.service
        query = compile_tags(body.tags, service.records, settings.reference_date)
        # Reuse executor validation, without returning a preview or correctness.
        execute([], query)
        return query

    @router.get("/tasks", tags=["Данные"])
    def tasks(principal: Annotated[Principal, Depends(require_principal)]):
        app.state.access.researcher(principal)
        return [{"id": task.id, "level": task.level, "tci": task.tci, "prompt": task.prompt, "training": task.training}
                for task in app.state.service.catalog.values()]

    @router.post("/sessions", response_model=SessionView, tags=["Сессии"])
    def create_session(body: SessionCreate, principal: Annotated[Principal, Depends(require_principal)]):
        app.state.access.authorize_code(principal, body.participant_code)
        return app.state.service.create_session(body)

    @router.get("/sessions/{session_id}", response_model=SessionView, tags=["Сессии"])
    def session(session_id: str, principal: Annotated[Principal, Depends(require_principal)]):
        app.state.access.authorize_resource(principal, "session", session_id)
        return app.state.service.session(session_id)

    @router.get("/trials/{trial_id}", response_model=TrialView, tags=["Пробы"])
    def trial(trial_id: str, principal: Annotated[Principal, Depends(require_principal)]):
        app.state.access.authorize_resource(principal, "trial", trial_id)
        return app.state.service.trial(trial_id)

    @router.post("/trials/{trial_id}/start", response_model=TrialView, tags=["Пробы"])
    def start(trial_id: str, principal: Annotated[Principal, Depends(require_principal)]):
        app.state.access.authorize_resource(principal, "trial", trial_id)
        return app.state.service.start_trial(trial_id)

    @router.post("/trials/{trial_id}/preview", response_model=QueryResult, tags=["Пробы"])
    def preview(trial_id: str, body: Query, principal: Annotated[Principal, Depends(require_principal)]):
        app.state.access.authorize_resource(principal, "trial", trial_id)
        return app.state.service.preview(trial_id, body)

    @router.post("/trials/{trial_id}/attempts", response_model=AttemptView, tags=["Пробы"])
    def attempt(trial_id: str, body: AttemptInput, principal: Annotated[Principal, Depends(require_principal)]):
        app.state.access.authorize_resource(principal, "trial", trial_id)
        return app.state.service.submit(trial_id, body)

    @router.post("/trials/{trial_id}/m3-attempts", response_model=AttemptView, tags=["Пробы"])
    def m3_attempt(trial_id: str, body: M3AttemptInput, principal: Annotated[Principal, Depends(require_principal)]):
        app.state.access.authorize_resource(principal, "trial", trial_id)
        return app.state.service.submit_m3(trial_id, body)

    @router.post("/trials/{trial_id}/events", tags=["События"])
    def events(trial_id: str, body: EventBatch, principal: Annotated[Principal, Depends(require_principal)]):
        app.state.access.authorize_resource(principal, "trial", trial_id)
        return app.state.service.ingest_events(trial_id, body.events)

    @router.get("/attempts/{attempt_id}/export", tags=["Экспорт"])
    def export_attempt(attempt_id: str, principal: Annotated[Principal, Depends(require_principal)]):
        app.state.access.authorize_resource(principal, "attempt", attempt_id)
        content = app.state.service.export_attempt(attempt_id)
        return Response(content, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="result.csv"'})

    @router.get("/sessions/{session_id}/export", tags=["Экспорт"])
    def export_session(session_id: str, principal: Annotated[Principal, Depends(require_principal)]):
        app.state.access.authorize_resource(principal, "session", session_id)
        return Response(encode(app.state.service.export_session(session_id)), media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="session.json"'})

    @router.get("/sessions/{session_id}/metrics.csv", tags=["Экспорт"])
    def metrics(session_id: str, principal: Annotated[Principal, Depends(require_principal)]):
        app.state.access.authorize_resource(principal, "session", session_id)
        return Response(app.state.service.export_metrics(session_id), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="metrics.csv"'})

    def analytics_filters(principal: Annotated[Principal, Depends(require_principal)],
                          participant_code: str | None = None, mode: Mode | None = None,
                          level: Annotated[int | None, QueryParameter(ge=1, le=3)] = None,
                          include_practice: bool = False, completed_only: bool = False):
        app.state.access.researcher(principal)
        return AnalyticsFilter(participant_code=participant_code, mode=mode, level=level,
                               include_practice=include_practice, completed_only=completed_only)

    @router.get("/analytics", tags=["Аналитика"])
    def analytics(filters: Annotated[AnalyticsFilter, Depends(analytics_filters)]):
        return AnalyticsService(app.state.service).collect(filters)

    @router.get("/analytics/{table}.csv", tags=["Аналитика"])
    def analytics_csv(table: Literal["trials", "attempts", "events", "interpretations", "summary"], filters: Annotated[AnalyticsFilter, Depends(analytics_filters)]):
        snapshot = AnalyticsService(app.state.service).collect(filters)
        return Response(table_csv(snapshot, table), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{table}.csv"'})

    @router.get("/analytics/export.zip", tags=["Аналитика"])
    def analytics_zip(filters: Annotated[AnalyticsFilter, Depends(analytics_filters)]):
        snapshot = AnalyticsService(app.state.service).collect(filters)
        return Response(csv_bundle(snapshot), media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="experiment-csv.zip"'})

    @router.get("/analytics/export.xlsx", tags=["Аналитика"])
    def analytics_excel(filters: Annotated[AnalyticsFilter, Depends(analytics_filters)]):
        snapshot = AnalyticsService(app.state.service).collect(filters)
        return Response(excel_workbook(snapshot), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": 'attachment; filename="experiment.xlsx"'})

    app.include_router(router)
    return app

import logging
import sqlite3
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi import Query as QueryParameter
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from .config import Settings, load_settings
from .analytics import AnalyticsFilter, AnalyticsService, csv_bundle, excel_workbook, table_csv
from .contracts import (AttemptInput, AttemptView, EventBatch, M3AttemptInput, M4TranscriptionView, M5AttemptInput, Mode,
                        PreviewView, Query, QueryResult, SessionCreate, SessionView, TaskRecord, TrialView)
from .database import Database, encode
from .errors import DomainError
from .service import ExperimentService, now_ms
from .access import (AccessService, COOKIE_NAME, Principal, Signatures,
                     key_from_runtime as invitation_key_from_runtime, require_principal, same_origin)
from .manual_query import ManualTags, builders, compile_tags, suggestions
from .engine import execute
from .llm import OpenAICompatibleInterpreter, key_from_runtime as llm_key_from_runtime
from .agent import RouterAIAgent
from .terminology import display_text
from .asr import ASRError, ASR_STORAGE_KIND, RouterAIASR


LOGGER = logging.getLogger("uvicorn.error")


def create_app(settings: Settings | None = None, clock=now_ms, signatures_factory=None, m3_interpreter_factory=None, m5_agent_factory=None, asr_factory=None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app):
        signatures = signatures_factory() if signatures_factory is not None else Signatures(invitation_key_from_runtime())
        database = Database(settings)
        database.initialize()
        service = ExperimentService(database, clock)
        shared_key = None
        if m3_interpreter_factory is None:
            shared_key = llm_key_from_runtime()
            interpreter = OpenAICompatibleInterpreter(
                base_url=settings.llm_base_url, api_key=shared_key, model=settings.llm_model,
                temperature=settings.llm_temperature, timeout_seconds=settings.llm_timeout_seconds,
                records=service.records, reference_date=settings.reference_date,
            )
        else:
            interpreter = m3_interpreter_factory(settings, service.records)
        service.configure_m3(interpreter)
        if m5_agent_factory is not None:
            agent = m5_agent_factory(settings, service.records)
        elif m3_interpreter_factory is None:
            agent = RouterAIAgent(
                base_url=settings.llm_base_url, api_key=shared_key, model=settings.llm_model,
                temperature=settings.llm_temperature, timeout_seconds=settings.llm_timeout_seconds,
                max_steps=settings.m5_max_llm_steps, records=service.records, reference_date=settings.reference_date,
            )
        else:
            # Existing tests/in-process callers that replace M3 do not implicitly perform external M5 calls.
            agent = None
        service.configure_m5(agent)
        asr = asr_factory(settings) if asr_factory is not None else RouterAIASR(settings)
        app.state.service = service
        app.state.asr = asr
        app.state.access = AccessService(database, signatures, clock=lambda: clock() / 1000)
        yield

    app = FastAPI(title="Экспериментальный стенд — API", version="0.1.0", lifespan=lifespan)
    router = APIRouter(prefix="/api", dependencies=[Depends(require_principal), Depends(same_origin)])
    validation_asr_cache: dict[tuple[str, str], dict] = {}

    @app.middleware("http")
    async def private_responses(request: Request, call_next):
        response = await call_next(request)
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/access/enter", include_in_schema=False)
    def enter(invitation: str):
        principal = app.state.access.validate(invitation, "invite")
        target = urlsplit(settings.login_redirect_path)
        query = dict(parse_qsl(target.query)) | {"access": principal.id}
        response = RedirectResponse(urlunsplit(target._replace(query=urlencode(query))), status_code=303)
        credential = app.state.access.cookie(principal)
        for name in (COOKIE_NAME, f'{COOKIE_NAME}_{principal.id}'):
            response.set_cookie(name, credential, max_age=settings.access_cookie_seconds, httponly=True,
                                secure=settings.public_base_url.startswith("https://"), samesite="lax", path="/")
        return response

    @router.post("/access/logout", tags=["Доступ"])
    def logout(principal: Annotated[Principal, Depends(require_principal)]):
        response = Response(status_code=204)
        response.delete_cookie(COOKIE_NAME, path="/")
        response.delete_cookie(f'{COOKIE_NAME}_{principal.id}', path="/")
        return response

    @router.get("/me", tags=["Доступ"])
    def me(principal: Annotated[Principal, Depends(require_principal)]):
        validation_mode = principal.role == "participant" and principal.code.lower() == "admin"
        with app.state.service.database.transaction() as connection:
            sessions = [] if validation_mode else [dict(row) for row in connection.execute(
                "SELECT id, kind FROM sessions WHERE participant_code = ? ORDER BY created_ms, id", (principal.code,))]
        return {"participant_code": principal.code, "role": principal.role, "access_context": principal.id,
                "validation_mode": validation_mode,
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

    @app.get("/internal/asr-ready", include_in_schema=False)
    def asr_ready():
        return {"status": "ok", "protocol": app.state.asr.protocol}

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
                "instruction": "Вся сборка запроса — в одной строке. Введите поле и значение, подтверждая Enter или Tab. После готового условия можно ввести «ИЛИ» и добавить значение того же поля, либо «И» и начать другое условие. Группировка, Итог, Экстремум и Экспорт вводятся здесь же. Сначала откройте предпросмотр, проверьте таблицу и выполненный Query, затем отдельно отправьте итоговый ответ.",
                "examples": ["Статус: В работе ИЛИ На ревью", "Приоритет != Низкий", "Группировка: направление работ", "Итог: количество", "Экстремум: максимум", "Экспорт: CSV"],
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
        return [{"id": task.id, "level": task.level, "tci": task.tci, "prompt": display_text(task.prompt), "training": task.training}
                for task in app.state.service.catalog.values()]

    @router.get("/validation/tasks", tags=["Валидация"])
    def validation_tasks(principal: Annotated[Principal, Depends(require_principal)]):
        if principal.role != "participant" or principal.code.lower() != "admin":
            raise DomainError("validation_denied", "Режим валидации доступен только приглашению admin.", 403)
        service = app.state.service
        return [{
            "id": task.id, "level": task.level, "tci": task.tci, "prompt": display_text(task.prompt),
            "training": task.training, "query": task.query.model_dump(mode="json"),
            "result": service.truth[task.id].model_dump(mode="json"),
        } for task in service.catalog.values()]


    def require_validation_admin(principal: Principal) -> None:
        if principal.role != "participant" or principal.code.lower() != "admin":
            raise DomainError("validation_denied", "Режим валидации доступен только приглашению admin.", 403)

    @router.post("/validation/tasks/{task_id}/preview", response_model=QueryResult, tags=["Валидация"])
    def validation_preview(task_id: str, body: Query, principal: Annotated[Principal, Depends(require_principal)]):
        require_validation_admin(principal)
        return app.state.service.validation_preview(task_id, body)

    @router.post("/validation/tasks/{task_id}/attempts", response_model=AttemptView, tags=["Валидация"])
    def validation_attempt(task_id: str, body: AttemptInput, principal: Annotated[Principal, Depends(require_principal)]):
        require_validation_admin(principal)
        return app.state.service.validation_submit(task_id, body)

    @router.post("/validation/tasks/{task_id}/m3-preview", response_model=PreviewView, tags=["Валидация"])
    def validation_m3_preview(task_id: str, body: M3AttemptInput, principal: Annotated[Principal, Depends(require_principal)]):
        require_validation_admin(principal)
        return app.state.service.validation_preview_text(task_id, Mode.M3, body)

    @router.post("/validation/tasks/{task_id}/m3-attempts", response_model=AttemptView, tags=["Валидация"])
    def validation_m3_attempt(task_id: str, body: M3AttemptInput, principal: Annotated[Principal, Depends(require_principal)]):
        require_validation_admin(principal)
        return app.state.service.validation_submit_text(task_id, Mode.M3, body)

    @router.post("/validation/tasks/{task_id}/m4-preview", response_model=PreviewView, tags=["Валидация"])
    def validation_m4_preview(task_id: str, body: M3AttemptInput, principal: Annotated[Principal, Depends(require_principal)]):
        require_validation_admin(principal)
        return app.state.service.validation_preview_text(task_id, Mode.M4, body)

    @router.post("/validation/tasks/{task_id}/m4-attempts", response_model=AttemptView, tags=["Валидация"])
    def validation_m4_attempt(task_id: str, body: M3AttemptInput, principal: Annotated[Principal, Depends(require_principal)]):
        require_validation_admin(principal)
        return app.state.service.validation_submit_text(task_id, Mode.M4, body)

    @router.post("/validation/tasks/{task_id}/m5-preview", response_model=PreviewView, tags=["Валидация"])
    def validation_m5_preview(task_id: str, body: M3AttemptInput, principal: Annotated[Principal, Depends(require_principal)]):
        require_validation_admin(principal)
        return app.state.service.validation_preview_text(task_id, Mode.M5, body)

    @router.post("/validation/tasks/{task_id}/m5-attempts", response_model=AttemptView, tags=["Валидация"])
    def validation_m5_attempt(task_id: str, body: M3AttemptInput, principal: Annotated[Principal, Depends(require_principal)]):
        require_validation_admin(principal)
        return app.state.service.validation_submit_text(task_id, Mode.M5, body)


    @router.post("/validation/tasks/{task_id}/m4-transcribe", response_model=M4TranscriptionView, tags=["Валидация"])
    async def validation_m4_transcribe(
        task_id: str, request: Request, principal: Annotated[Principal, Depends(require_principal)],
        request_id: UUID = QueryParameter(...), duration_ms: int = QueryParameter(..., gt=0),
    ):
        require_validation_admin(principal)
        service = app.state.service
        if task_id not in service.catalog:
            raise DomainError("validation_task_not_found", "Задание для проверки не найдено.", 404)
        cache_key = (task_id, str(request_id))
        cached = validation_asr_cache.get(cache_key)
        if cached is not None:
            return cached
        if duration_ms > settings.asr_max_audio_seconds * 1000:
            raise DomainError("asr_audio_too_long", f"Запись должна быть не длиннее {settings.asr_max_audio_seconds} секунд.", 413)
        mime_type = request.headers.get("content-type", "").strip()
        base_type = mime_type.split(";", 1)[0].lower()
        if base_type not in {"audio/webm", "audio/ogg", "audio/mp4", "audio/x-m4a", "audio/wav", "audio/wave", "audio/x-wav"}:
            raise DomainError("asr_audio_type", "Браузер прислал неподдерживаемый формат аудио.", 415)
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > settings.asr_max_audio_bytes:
            raise DomainError("asr_audio_too_large", "Аудиозапись слишком большая.", 413)
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > settings.asr_max_audio_bytes:
                raise DomainError("asr_audio_too_large", "Аудиозапись слишком большая.", 413)
            chunks.append(chunk)
        audio = b"".join(chunks)
        if not audio:
            raise DomainError("asr_audio_empty", "Браузер не записал звук. Повторите запись.", 422)
        try:
            result = await run_in_threadpool(app.state.asr.transcribe, audio, mime_type)
        except ASRError as exc:
            raise DomainError("asr_failed", str(exc), 502) from exc
        if not result.text.strip():
            raise DomainError("asr_no_speech", "Речь не распознана. Повторите запись ближе к микрофону.", 422)
        response = {
            "request_id": str(request_id),
            "text": result.text.strip(),
            "model": settings.asr_model,
            "detected_language": result.detected_language,
            "language_probability": result.language_probability,
            "asr_ms": result.asr_ms,
        }
        # Only the transcript stays in RAM for idempotent admin retries; raw audio is discarded immediately.
        validation_asr_cache[cache_key] = response
        return response

    @router.post("/sessions", response_model=SessionView, tags=["Сессии"])
    def create_session(body: SessionCreate, principal: Annotated[Principal, Depends(require_principal)]):
        app.state.access.authorize_code(principal, body.participant_code)
        if principal.role == "participant" and principal.code.lower() == "admin":
            raise DomainError("validation_mode", "Приглашение admin предназначено только для проверки формулировок и не создаёт экспериментальные сессии.", 403)
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

    @router.post("/trials/{trial_id}/m3-preview", response_model=PreviewView, tags=["Пробы"])
    def m3_preview(trial_id: str, body: M3AttemptInput, principal: Annotated[Principal, Depends(require_principal)]):
        app.state.access.authorize_resource(principal, "trial", trial_id)
        return app.state.service.preview_m3(trial_id, body)

    @router.post("/trials/{trial_id}/m3-attempts", response_model=AttemptView, tags=["Пробы"])
    def m3_attempt(trial_id: str, body: M3AttemptInput, principal: Annotated[Principal, Depends(require_principal)]):
        app.state.access.authorize_resource(principal, "trial", trial_id)
        return app.state.service.submit_m3(trial_id, body)

    @router.post("/trials/{trial_id}/m4-transcribe", response_model=M4TranscriptionView, tags=["Пробы"])
    async def m4_transcribe(
        trial_id: str, request: Request, principal: Annotated[Principal, Depends(require_principal)],
        request_id: UUID = QueryParameter(...), duration_ms: int = QueryParameter(..., gt=0),
    ):
        app.state.access.authorize_resource(principal, "trial", trial_id)
        service = app.state.service
        cached = service.cached_m4_transcription(trial_id, str(request_id))
        if cached is not None:
            return cached
        if duration_ms > settings.asr_max_audio_seconds * 1000:
            raise DomainError("asr_audio_too_long", f"Запись должна быть не длиннее {settings.asr_max_audio_seconds} секунд.", 413)
        mime_type = request.headers.get("content-type", "").strip()
        base_type = mime_type.split(";", 1)[0].lower()
        if base_type not in {"audio/webm", "audio/ogg", "audio/mp4", "audio/x-m4a", "audio/wav", "audio/wave", "audio/x-wav"}:
            raise DomainError("asr_audio_type", "Браузер прислал неподдерживаемый формат аудио.", 415)
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > settings.asr_max_audio_bytes:
            raise DomainError("asr_audio_too_large", "Аудиозапись слишком большая.", 413)
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > settings.asr_max_audio_bytes:
                raise DomainError("asr_audio_too_large", "Аудиозапись слишком большая.", 413)
            chunks.append(chunk)
        audio = b"".join(chunks)
        if not audio:
            raise DomainError("asr_audio_empty", "Браузер не записал звук. Повторите запись.", 422)
        started_ms = service.clock()
        if settings.logging.asrLogging:
            LOGGER.info("[m4-asr] start trial_id=%s request_id=%s bytes=%s duration_ms=%s mime=%s model=%s",
                        trial_id, request_id, len(audio), duration_ms, mime_type, settings.asr_model)
        try:
            result = await run_in_threadpool(app.state.asr.transcribe, audio, mime_type)
        except ASRError as exc:
            if settings.logging.asrLogging:
                LOGGER.exception("[m4-asr] transcription failed trial_id=%s request_id=%s", trial_id, request_id)
            raise DomainError("asr_failed", str(exc), 502) from exc
        finished_ms = service.clock()
        if not result.text.strip():
            if settings.logging.asrLogging:
                LOGGER.info("[m4-asr] no speech trial_id=%s request_id=%s asr_ms=%.1f", trial_id, request_id, result.asr_ms)
            raise DomainError("asr_no_speech", "Речь не распознана. Повторите запись ближе к микрофону.", 422)
        response = service.save_m4_transcription(
            trial_id=trial_id, request_id=str(request_id), mime_type=mime_type, audio_bytes=len(audio),
            audio_duration_ms=duration_ms, model=settings.asr_model, compute_type=ASR_STORAGE_KIND,
            requested_language=settings.asr_language, detected_language=result.detected_language,
            language_probability=result.language_probability, transcript=result.text.strip(), asr_ms=result.asr_ms,
            started_ms=started_ms, finished_ms=finished_ms,
        )
        if settings.logging.asrLogging:
            LOGGER.info("[m4-asr] success trial_id=%s request_id=%s asr_ms=%.1f chars=%s transcript=%r",
                        trial_id, request_id, result.asr_ms, len(result.text.strip()), result.text.strip())
        return response

    @router.post("/trials/{trial_id}/m4-preview", response_model=PreviewView, tags=["Пробы"])
    def m4_preview(trial_id: str, body: M3AttemptInput, principal: Annotated[Principal, Depends(require_principal)]):
        app.state.access.authorize_resource(principal, "trial", trial_id)
        return app.state.service.preview_m4(trial_id, body)

    @router.post("/trials/{trial_id}/m4-attempts", response_model=AttemptView, tags=["Пробы"])
    def m4_attempt(trial_id: str, body: M3AttemptInput, principal: Annotated[Principal, Depends(require_principal)]):
        app.state.access.authorize_resource(principal, "trial", trial_id)
        return app.state.service.submit_m4(trial_id, body)

    @router.post("/trials/{trial_id}/m5-preview", response_model=PreviewView, tags=["Пробы"])
    def m5_preview(trial_id: str, body: M5AttemptInput, principal: Annotated[Principal, Depends(require_principal)]):
        app.state.access.authorize_resource(principal, "trial", trial_id)
        return app.state.service.preview_m5(trial_id, body)

    @router.post("/trials/{trial_id}/m5-attempts", response_model=AttemptView, tags=["Пробы"])
    def m5_attempt(trial_id: str, body: M5AttemptInput, principal: Annotated[Principal, Depends(require_principal)]):
        app.state.access.authorize_resource(principal, "trial", trial_id)
        return app.state.service.submit_m5(trial_id, body)

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


    @router.get("/sessions/{session_id}/completion-code.txt", tags=["Экспорт"])
    def completion_code_file(session_id: str, principal: Annotated[Principal, Depends(require_principal)]):
        app.state.access.authorize_resource(principal, "session", session_id)
        session_view = app.state.service.session(session_id)
        code = session_view.get("completion_code")
        if session_view["kind"] != "experiment" or not session_view["complete"] or not code:
            raise DomainError("completion_code_unavailable", "Проверочный код появляется после завершения основной сессии.", 409)
        content = f"Код участника: {session_view['participant_code']}\nПроверочный код: {code}\n"
        return Response(content, media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="completion-code.txt"'})

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
    def analytics_csv(table: Literal["registry", "trials", "attempts", "events", "interpretations", "m4_transcriptions", "agent_runs", "summary"], filters: Annotated[AnalyticsFilter, Depends(analytics_filters)]):
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

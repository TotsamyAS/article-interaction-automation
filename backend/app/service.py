import csv
import io
import json
import time
from decimal import Decimal
from uuid import uuid4

from .catalog import build_catalog
from .contracts import (BASE_AVAILABLE_MODES, AttemptInput, Event, M3AttemptInput, M5AttemptInput, Mode, Query, QueryResult,
                        SessionCreate, TaskRecord)
from .database import Database, encode
from .engine import csv_result, execute, verify
from .errors import DomainError
from .metrics import active_time_ms, trial_metrics
from .llm import LLMError, PROMPT_VERSION
from .agent import AgentError, AGENT_PROMPT_VERSION
from .terminology import WORDING_VERSION, display_text


def now_ms() -> int:
    return time.time_ns() // 1_000_000


class ExperimentService:
    def __init__(self, database: Database, clock=now_ms, m3_interpreter=None, m5_agent=None):
        self.database = database
        self.settings = database.settings
        self.clock = clock
        self.m3_interpreter = None
        self.m5_agent = None
        self.available_modes = set(BASE_AVAILABLE_MODES)
        self.configure_m3(m3_interpreter)
        self.configure_m5(m5_agent)
        self.catalog = build_catalog(self.settings.reference_date)
        with database.transaction() as connection:
            snapshot = connection.execute("SELECT * FROM dataset WHERE singleton = 1").fetchone()
        self.records = [TaskRecord.model_validate(row) for row in json.loads(snapshot["records"])]
        self.truth = {key: QueryResult.model_validate(value) for key, value in json.loads(snapshot["ground_truth"]).items()}
        self.manifest = json.loads(snapshot["manifest"])
        with database.transaction() as connection:
            self.manifest["protocol_changes"] = [dict(row) for row in connection.execute(
                "SELECT * FROM protocol_changes ORDER BY id")]

    def configure_m3(self, interpreter) -> None:
        self.m3_interpreter = interpreter
        for mode in (Mode.M3, Mode.M4):
            if interpreter is None:
                self.available_modes.discard(mode)
            else:
                self.available_modes.add(mode)

    def configure_m5(self, agent) -> None:
        self.m5_agent = agent
        if agent is None:
            self.available_modes.discard(Mode.M5)
        else:
            self.available_modes.add(Mode.M5)

    @staticmethod
    def _trial(connection, trial_id):
        row = connection.execute("SELECT * FROM trials WHERE id = ?", (trial_id,)).fetchone()
        if row is None:
            raise DomainError("trial_not_found", "Проба не найдена.", 404)
        return dict(row)

    @staticmethod
    def _session(connection, session_id):
        row = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise DomainError("session_not_found", "Сессия не найдена.", 404)
        return dict(row)

    def _expire(self, connection, trial, now):
        if trial["status"] == "active" and now >= trial["started_ms"] + trial["trial_limit_seconds"] * 1000:
            self._finish(connection, trial, "incomplete", "time_limit",
                         trial["started_ms"] + trial["trial_limit_seconds"] * 1000)

    @staticmethod
    def _finish(connection, trial, status, reason, ended):
        connection.execute("UPDATE trials SET status = ?, end_reason = ?, ended_ms = ? WHERE id = ?",
                           (status, reason, ended, trial["id"]))
        trial.update(status=status, end_reason=reason, ended_ms=ended)

    def _trial_view(self, connection, trial):
        attempts = [dict(row) for row in connection.execute(
            "SELECT id, ordinal, correct, started_ms, finished_ms FROM attempts WHERE trial_id = ? ORDER BY ordinal", (trial["id"],))]
        event_rows = connection.execute("SELECT payload FROM events WHERE trial_id = ? ORDER BY sequence", (trial["id"],)).fetchall()
        events = [Event.model_validate_json(row["payload"]) for row in event_rows]
        next_event_sequence = events[-1].sequence + 1 if events else 0
        last_event_offset_ms = events[-1].offset_ms if events else 0
        end = trial["ended_ms"] if trial["ended_ms"] is not None else self.clock()
        duration = max(0, end - trial["started_ms"]) if trial["started_ms"] is not None else 0
        active = active_time_ms(events, duration, self.settings.idle_threshold_ms)
        definition = self.catalog[trial["task_id"]]
        prompt = display_text(definition.prompt) if trial['wording_version'] == WORDING_VERSION else definition.prompt
        return {**trial, "prompt": prompt if trial["status"] != "pending" else None,
                "tci": definition.tci, "level": definition.level,
                "mode_available": Mode(trial["mode"]) in self.available_modes,
                "deadline_ms": trial["started_ms"] + trial["trial_limit_seconds"] * 1000 if trial["started_ms"] is not None else None,
                "attempts": attempts,
                "next_event_sequence": next_event_sequence,
                "last_event_offset_ms": last_event_offset_ms,
                "elapsed_since_start_ms": duration,
                "metrics": trial_metrics(trial, attempts, active, trial["trial_limit_seconds"] * 1000, trial["attempt_limit"])}

    def _session_view(self, connection, session):
        rows = [dict(row) for row in connection.execute("SELECT * FROM trials WHERE session_id = ? ORDER BY position", (session["id"],))]
        for trial in rows:
            self._expire(connection, trial, self.clock())
        return {**session, "complete": all(row["status"] in ("correct", "incomplete") for row in rows),
                "manifest": self.manifest, "trials": [self._trial_view(connection, row) for row in rows]}

    def create_session(self, request: SessionCreate):
        with self.database.transaction() as connection:
            existing = connection.execute("SELECT * FROM sessions WHERE participant_code = ? AND kind = ?",
                                          (request.participant_code, request.kind)).fetchone()
            if existing:
                return self._session_view(connection, dict(existing))
            sequence = connection.execute("SELECT COUNT(*) FROM sessions WHERE kind = 'experiment'").fetchone()[0] if request.kind == "experiment" else 0
            session_id = str(uuid4())
            connection.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
                               (session_id, request.participant_code, request.kind, sequence, self.clock()))
            position = 0
            for block in range(5):
                mode = f"M{1 + (block + sequence) % 5}"
                variant = chr(ord("a") + (block + 2 * sequence) % 5)
                task_ids = [f"C{level}{variant}" for level in (1, 2, 3)] if request.kind == "experiment" else ["TRAIN"]
                for task_id in task_ids:
                    connection.execute(
                        "INSERT INTO trials (id, session_id, position, block_index, mode, task_id, trial_limit_seconds, attempt_limit, wording_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (str(uuid4()), session_id, position, block, mode, task_id, self.settings.trial_limit_seconds, self.settings.attempt_limit, WORDING_VERSION))
                    position += 1
            return self._session_view(connection, self._session(connection, session_id))

    def session(self, session_id):
        with self.database.transaction() as connection:
            return self._session_view(connection, self._session(connection, session_id))

    def trial(self, trial_id):
        with self.database.transaction() as connection:
            trial = self._trial(connection, trial_id)
            self._expire(connection, trial, self.clock())
            return self._trial_view(connection, trial)

    def start_trial(self, trial_id):
        with self.database.transaction() as connection:
            trial = self._trial(connection, trial_id)
            self._expire(connection, trial, self.clock())
            if trial["status"] != "pending":
                return self._trial_view(connection, trial)
            if Mode(trial["mode"]) not in self.available_modes:
                raise DomainError("mode_unavailable", "Этот режим ожидает выбора и подключения системы интерпретации.", 409)
            preceding = connection.execute("SELECT * FROM trials WHERE session_id = ? AND position < ? ORDER BY position DESC",
                                           (trial["session_id"], trial["position"])).fetchall()
            for row in preceding:
                previous = dict(row)
                self._expire(connection, previous, self.clock())
                if previous["status"] not in ("correct", "incomplete"):
                    raise DomainError("trial_order", "Сначала завершите предыдущую пробу.", 409)
            connection.execute("UPDATE trials SET status = 'active', started_ms = ? WHERE id = ?", (self.clock(), trial_id))
            return self._trial_view(connection, self._trial(connection, trial_id))

    def preview(self, trial_id, query: Query):
        """Execute a reversible preview for the manual modes without creating an attempt."""
        with self.database.transaction() as connection:
            trial = self._trial(connection, trial_id)
            self._expire(connection, trial, self.clock())
            if trial["status"] != "active":
                raise DomainError("trial_not_active", "Проба не активна или её время истекло.", 409)
            if trial["mode"] not in (Mode.M1.value, Mode.M2.value):
                raise DomainError("preview_not_available", "Для этого режима используйте его endpoint предпросмотра.", 409)
            return execute(self.records, query)

    def _preview_payload(self, request_id, query: Query):
        result = execute(self.records, query)
        return {"request_id": str(request_id), "query": query.model_dump(mode="json"),
                "result": result.model_dump(mode="json")}

    def submit(self, trial_id, request: AttemptInput):
        query_json = encode(request.query.model_dump(mode="json"))
        with self.database.transaction() as connection:
            trial = self._trial(connection, trial_id)
            previous = connection.execute("SELECT * FROM attempts WHERE trial_id = ? AND request_id = ?",
                                          (trial_id, str(request.request_id))).fetchone()
            if previous:
                if previous["query_json"] != query_json:
                    raise DomainError("idempotency_conflict", "request_id уже использован с другим запросом.", 409)
                return json.loads(previous["response_json"])
            self._expire(connection, trial, self.clock())
            if trial["status"] != "active":
                raise DomainError("trial_not_active", "Проба не активна или её время истекло.", 409)
            if Mode(trial["mode"]) not in self.available_modes:
                raise DomainError("mode_unavailable", "Режим не подключён.", 409)
            started = self.clock()
            operation_started = time.perf_counter_ns()
            result = execute(self.records, request.query)
            content = csv_result(result, request.query) if request.query.output.format == "csv" else None
            definition = self.catalog[trial["task_id"]]
            correct = verify(result, self.truth[definition.id], check_aggregate=definition.level > 1,
                             average=definition.query.grouping.aggregation == "AVG",
                             tolerance=Decimal(self.settings.average_tolerance),
                             requires_export=definition.level == 3, exported=content is not None and request.query.output.scope != "aggregate_only")
            exec_ms = (time.perf_counter_ns() - operation_started) / 1_000_000
            finished = self.clock()
            ordinal = connection.execute("SELECT COUNT(*) FROM attempts WHERE trial_id = ?", (trial_id,)).fetchone()[0] + 1
            attempt_id = str(uuid4())
            if finished >= trial["started_ms"] + trial["trial_limit_seconds"] * 1000:
                correct = False
                self._expire(connection, trial, finished)
            elif correct:
                self._finish(connection, trial, "correct", "correct", finished)
            elif ordinal >= trial["attempt_limit"]:
                self._finish(connection, trial, "incomplete", "attempt_limit", finished)
            response = {"id": attempt_id, "trial_id": trial_id, "ordinal": ordinal,
                        "correct": correct, "trial_status": trial["status"], "end_reason": trial["end_reason"],
                        "result": result.model_dump(mode="json"), "Texec_ms": exec_ms,
                        "started_ms": started, "finished_ms": finished,
                        "export_url": f"/api/attempts/{attempt_id}/export" if content is not None else None}
            connection.execute("INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (attempt_id, trial_id, str(request.request_id), ordinal, query_json, encode(response),
                                int(correct), started, finished, content))
            return response

    def preview_m3(self, trial_id: str, request: M3AttemptInput):
        return self._llm_request(trial_id, request, Mode.M3, finalize=False)

    def submit_m3(self, trial_id: str, request: M3AttemptInput):
        return self._llm_request(trial_id, request, Mode.M3, finalize=True)

    def preview_m4(self, trial_id: str, request: M3AttemptInput):
        return self._llm_request(trial_id, request, Mode.M4, finalize=False)

    def submit_m4(self, trial_id: str, request: M3AttemptInput):
        return self._llm_request(trial_id, request, Mode.M4, finalize=True)

    def _llm_request(self, trial_id: str, request: M3AttemptInput, mode: Mode, *, finalize: bool):
        request_id = str(request.request_id)
        with self.database.transaction() as connection:
            trial = self._trial(connection, trial_id)
            previous = connection.execute(
                "SELECT * FROM m3_interpretations WHERE trial_id = ? AND request_id = ?", (trial_id, request_id)
            ).fetchone()
            if previous:
                previous = dict(previous)
                if previous["user_text"] != request.text:
                    raise DomainError("idempotency_conflict", "request_id уже использован с другим текстом.", 409)
                if previous["status"] == "pending":
                    raise DomainError("request_in_progress", "Этот запрос к LLM ещё выполняется.", 409)
                if previous["status"] != "ok":
                    status = 503 if previous["error_code"] == "llm_unavailable" else 502
                    raise DomainError(previous["error_code"] or "llm_failed", previous["error_message"] or "LLM не смог интерпретировать запрос.", status)
                query = Query.model_validate_json(previous["query_json"])
            else:
                self._expire(connection, trial, self.clock())
                if trial["status"] != "active":
                    raise DomainError("trial_not_active", "Проба не активна или её время истекло.", 409)
                if trial["mode"] != mode.value:
                    raise DomainError("mode_mismatch", f"Этот endpoint доступен только в {mode.value}.", 409)
                if mode not in self.available_modes or self.m3_interpreter is None:
                    raise DomainError("mode_unavailable", f"{mode.value} не настроен.", 409)
                changed_config = connection.execute(
                    "SELECT 1 FROM m3_interpretations "
                    "WHERE requested_model != ? OR prompt_version != ? "
                    "OR (prompt_sha256 IS NOT NULL AND prompt_sha256 != ?) "
                    "OR (temperature IS NOT NULL AND temperature != ?) LIMIT 1",
                    (self.m3_interpreter.model, PROMPT_VERSION, self.m3_interpreter.prompt_sha256, self.m3_interpreter.temperature),
                ).fetchone()
                if changed_config:
                    raise DomainError(
                        "m3_protocol_changed",
                        "Конфигурация общего LLM-компилятора M3/M4 изменилась после начала сбора данных. Используйте отдельный том для нового эксперимента.",
                        409,
                    )
                started = self.clock()
                connection.execute(
                    "INSERT INTO m3_interpretations "
                    "(id, trial_id, request_id, user_text, status, requested_model, prompt_version, prompt_sha256, temperature, started_ms) "
                    "VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
                    (str(uuid4()), trial_id, request_id, request.text, self.m3_interpreter.model, PROMPT_VERSION,
                     self.m3_interpreter.prompt_sha256, self.m3_interpreter.temperature, started),
                )
                query = None
        if query is not None:
            if finalize:
                return self.submit(trial_id, AttemptInput(request_id=request.request_id, query=query))
            return self._preview_payload(request.request_id, query)

        try:
            interpretation = self.m3_interpreter.interpret(request.text)
        except LLMError as error:
            finished = self.clock()
            state = "invalid_response" if error.code == "llm_invalid_response" else "provider_error"
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE m3_interpretations SET status = ?, response_model = ?, provider = ?, system_fingerprint = ?, "
                    "raw_response = ?, error_code = ?, error_message = ?, llm_ms = ?, input_tokens = ?, output_tokens = ?, finished_ms = ? "
                    "WHERE trial_id = ? AND request_id = ?",
                    (state, error.response_model, error.provider, error.system_fingerprint, error.raw_response, error.code,
                     error.message, error.llm_ms, error.input_tokens, error.output_tokens, finished, trial_id, request_id),
                )
            raise DomainError(error.code, error.message, error.status) from None

        query_json = encode(interpretation.query.model_dump(mode="json"))
        try:
            execute([], interpretation.query)
        except DomainError:
            finished = self.clock()
            message = "LLM сформировал несовместимый Query. Переформулируйте запрос."
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE m3_interpretations SET status = 'invalid_query', response_model = ?, provider = ?, system_fingerprint = ?, "
                    "raw_response = ?, query_json = ?, error_code = 'llm_invalid_query', error_message = ?, llm_ms = ?, "
                    "input_tokens = ?, output_tokens = ?, finished_ms = ? WHERE trial_id = ? AND request_id = ?",
                    (interpretation.response_model, interpretation.provider, interpretation.system_fingerprint,
                     interpretation.raw_response, query_json, message, interpretation.llm_ms, interpretation.input_tokens,
                     interpretation.output_tokens, finished, trial_id, request_id),
                )
            raise DomainError("llm_invalid_query", message, 502) from None

        finished = self.clock()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE m3_interpretations SET status = 'ok', response_model = ?, provider = ?, system_fingerprint = ?, "
                "raw_response = ?, query_json = ?, llm_ms = ?, input_tokens = ?, output_tokens = ?, finished_ms = ? "
                "WHERE trial_id = ? AND request_id = ?",
                (interpretation.response_model, interpretation.provider, interpretation.system_fingerprint,
                 interpretation.raw_response, query_json, interpretation.llm_ms, interpretation.input_tokens,
                 interpretation.output_tokens, finished, trial_id, request_id),
            )
        if finalize:
            return self.submit(trial_id, AttemptInput(request_id=request.request_id, query=interpretation.query))
        return self._preview_payload(request.request_id, interpretation.query)

    def preview_m5(self, trial_id: str, request: M5AttemptInput):
        return self._m5_request(trial_id, request, finalize=False)

    def submit_m5(self, trial_id: str, request: M5AttemptInput):
        return self._m5_request(trial_id, request, finalize=True)

    def _m5_request(self, trial_id: str, request: M5AttemptInput, *, finalize: bool):
        request_id = str(request.request_id)
        with self.database.transaction() as connection:
            trial = self._trial(connection, trial_id)
            previous = connection.execute(
                "SELECT * FROM m5_agent_runs WHERE trial_id = ? AND request_id = ?", (trial_id, request_id)
            ).fetchone()
            if previous:
                previous = dict(previous)
                if previous["user_text"] != request.text:
                    raise DomainError("idempotency_conflict", "request_id уже использован с другой целью.", 409)
                if previous["status"] == "pending":
                    raise DomainError("request_in_progress", "Этот агентный запуск ещё выполняется.", 409)
                if previous["status"] != "ok":
                    status = 503 if previous["error_code"] == "agent_unavailable" else 502
                    raise DomainError(previous["error_code"] or "agent_failed", previous["error_message"] or "Агент не смог выполнить запрос.", status)
                query = Query.model_validate_json(previous["query_json"])
            else:
                self._expire(connection, trial, self.clock())
                if trial["status"] != "active":
                    raise DomainError("trial_not_active", "Проба не активна или её время истекло.", 409)
                if trial["mode"] != Mode.M5.value:
                    raise DomainError("mode_mismatch", "Агентное выполнение доступно только в M5.", 409)
                if Mode.M5 not in self.available_modes or self.m5_agent is None:
                    raise DomainError("mode_unavailable", "M5 не настроен.", 409)
                changed_config = connection.execute(
                    "SELECT 1 FROM m5_agent_runs WHERE requested_model != ? OR prompt_version != ? OR prompt_sha256 != ? "
                    "OR temperature != ? OR max_steps != ? LIMIT 1",
                    (self.m5_agent.model, AGENT_PROMPT_VERSION, self.m5_agent.prompt_sha256, self.m5_agent.temperature, self.m5_agent.max_steps),
                ).fetchone()
                if changed_config:
                    raise DomainError("m5_protocol_changed", "Конфигурация M5 изменилась после начала сбора данных. Используйте отдельный том для нового эксперимента.", 409)
                started = self.clock()
                connection.execute(
                    "INSERT INTO m5_agent_runs (id, trial_id, request_id, user_text, status, requested_model, prompt_version, prompt_sha256, temperature, max_steps, started_ms) "
                    "VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)",
                    (str(uuid4()), trial_id, request_id, request.text, self.m5_agent.model, AGENT_PROMPT_VERSION,
                     self.m5_agent.prompt_sha256, self.m5_agent.temperature, self.m5_agent.max_steps, started),
                )
                query = None
        if query is not None:
            if finalize:
                return self.submit(trial_id, AttemptInput(request_id=request.request_id, query=query))
            return self._preview_payload(request.request_id, query)

        try:
            execution = self.m5_agent.run(request.text)
        except AgentError as error:
            finished = self.clock(); d = error.diagnostics
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE m5_agent_runs SET status = ?, error_code = ?, error_message = ?, llm_ms = ?, finished_ms = ? "
                    "WHERE trial_id = ? AND request_id = ?",
                    ("provider_error" if error.code in ("agent_provider_error", "agent_unavailable") else "agent_error",
                     error.code, error.message, d.get("llm_ms"), finished, trial_id, request_id),
                )
            raise DomainError(error.code, error.message, error.status) from None
        except Exception as error:
            finished = self.clock(); message = f"Agent loop завершился ошибкой: {type(error).__name__}."
            with self.database.transaction() as connection:
                connection.execute("UPDATE m5_agent_runs SET status='agent_error', error_code='agent_exception', error_message=?, finished_ms=? WHERE trial_id=? AND request_id=?",
                                   (message, finished, trial_id, request_id))
            raise DomainError("agent_exception", message, 502) from None

        query_json = encode(execution.query.model_dump(mode="json")); trajectory_json = encode(execution.trajectory); finished = self.clock()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE m5_agent_runs SET status='ok', response_model=?, provider=?, query_json=?, trajectory_json=?, final_text=?, termination=?, "
                "llm_ms=?, tool_ms=?, total_ms=?, llm_calls=?, tool_calls=?, input_tokens=?, output_tokens=?, finished_ms=? "
                "WHERE trial_id=? AND request_id=?",
                (execution.response_model, execution.provider, query_json, trajectory_json, execution.final_text, execution.termination,
                 execution.llm_ms, execution.tool_ms, execution.total_ms, execution.llm_calls, execution.tool_calls, execution.input_tokens,
                 execution.output_tokens, finished, trial_id, request_id),
            )
        if finalize:
            return self.submit(trial_id, AttemptInput(request_id=request.request_id, query=execution.query))
        return self._preview_payload(request.request_id, execution.query)

    def ingest_events(self, trial_id, events: list[Event]):
        with self.database.transaction() as connection:
            trial = self._trial(connection, trial_id)
            self._expire(connection, trial, self.clock())
            if trial["started_ms"] is None:
                raise DomainError("trial_not_started", "Сначала начните пробу.", 409)
            end = trial["ended_ms"] if trial["ended_ms"] is not None else self.clock()
            max_offset = max(0, end - trial["started_ms"])
            previous = connection.execute("SELECT sequence, offset_ms FROM events WHERE trial_id = ? ORDER BY sequence DESC LIMIT 1", (trial_id,)).fetchone()
            last_seq, last_offset = (previous["sequence"], previous["offset_ms"]) if previous else (-1, -1)
            accepted = 0
            for event in events:
                payload = event.model_dump_json()
                duplicate = connection.execute("SELECT payload FROM events WHERE trial_id = ? AND event_id = ?", (trial_id, str(event.event_id))).fetchone()
                if duplicate:
                    if duplicate["payload"] != payload:
                        raise DomainError("event_conflict", "Не удалось сохранить журнал действий: повторно отправленное действие изменилось. Сообщите исследователю.", 409)
                    continue
                if event.sequence <= last_seq:
                    raise DomainError("event_sequence", "Не удалось сохранить журнал действий: эта проба уже обновлена, возможно, в другой вкладке. Оставьте одну вкладку стенда и обновите страницу.", 409)
                if event.offset_ms < last_offset:
                    raise DomainError("event_time_order", "Не удалось сохранить журнал действий: время действий записано в неправильном порядке. Сообщите исследователю и обновите страницу.", 409)
                if event.offset_ms > max_offset:
                    raise DomainError("event_outside_trial", "Не удалось сохранить журнал действий: действие записано за пределами времени этой пробы. Сообщите исследователю и обновите страницу.", 409)
                connection.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
                                   (trial_id, str(event.event_id), event.sequence, event.offset_ms, payload, self.clock()))
                last_seq, last_offset = event.sequence, event.offset_ms
                accepted += 1
            return {"accepted": accepted, "duplicates": len(events) - accepted}

    def export_attempt(self, attempt_id):
        with self.database.transaction() as connection:
            row = connection.execute("SELECT csv_text FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
            if row is None or row["csv_text"] is None:
                raise DomainError("export_not_found", "Экспорт не найден.", 404)
            return row["csv_text"]

    def export_session(self, session_id):
        # A snapshot includes raw events and attempts, not only derived summaries.
        with self.database.transaction() as connection:
            return self.session_snapshot(connection, self._session(connection, session_id))

    def session_snapshot(self, connection, session):
        session_id = session["id"]
        result = self._session_view(connection, session)
        result["attempt_log"] = [json.loads(row["response_json"]) | {"query": json.loads(row["query_json"])}
                                 for row in connection.execute("SELECT a.* FROM attempts a JOIN trials t ON t.id = a.trial_id WHERE t.session_id = ? ORDER BY t.position, a.ordinal", (session_id,))]
        result["event_log"] = [dict(row) | {"payload": json.loads(row["payload"])} for row in connection.execute(
            "SELECT e.* FROM events e JOIN trials t ON t.id = e.trial_id WHERE t.session_id = ? ORDER BY t.position, e.sequence", (session_id,))]
        result["interpretation_log"] = [dict(row) | {"query": json.loads(row["query_json"]) if row["query_json"] else None}
                                        for row in connection.execute(
            "SELECT i.* FROM m3_interpretations i JOIN trials t ON t.id = i.trial_id WHERE t.session_id = ? ORDER BY t.position, i.started_ms, i.id", (session_id,))]
        result["agent_log"] = [dict(row) | {"query": json.loads(row["query_json"]) if row["query_json"] else None,
                                             "trajectory": json.loads(row["trajectory_json"]) if row["trajectory_json"] else None}
                               for row in connection.execute(
            "SELECT a.* FROM m5_agent_runs a JOIN trials t ON t.id = a.trial_id WHERE t.session_id = ? ORDER BY t.position, a.started_ms, a.id", (session_id,))]
        return result

    def export_metrics(self, session_id):
        session = self.session(session_id)
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["participant", "kind", "trial_id", "mode", "task", "status", "reason", "trial_limit_seconds", "attempt_limit", "wording_version",
                         "elapsed_ms", "Tcorrect_actual_ms", "Tfirst_ms", "Tuser_active_ms", "A1_actual", "attempts", "Nretry_actual",
                         "Tcorrect_analysis_ms", "A1_analysis", "Nretry_analysis"])
        for trial in session["trials"]:
            actual, analysis = trial["metrics"]["actual"], trial["metrics"]["analysis"]
            writer.writerow([session["participant_code"], session["kind"], trial["id"], trial["mode"], trial["task_id"], trial["status"], trial["end_reason"],
                             trial['trial_limit_seconds'], trial['attempt_limit'], trial['wording_version'],
                             actual["elapsed_ms"], actual["Tcorrect_ms"], actual["Tfirst_ms"], actual["Tuser_active_ms"], actual["A1"], actual["attempts"], actual["Nretry"],
                             analysis["Tcorrect_ms"], analysis["A1"], analysis["Nretry"]])
        return "\ufeff" + stream.getvalue()

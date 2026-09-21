import csv
import io
import json
import time
from decimal import Decimal
from uuid import uuid4

from .catalog import build_catalog
from .contracts import (AVAILABLE_MODES, AttemptInput, Event, Mode, Query, QueryResult,
                        SessionCreate, TaskRecord)
from .database import Database, encode
from .engine import csv_result, execute, verify
from .errors import DomainError
from .metrics import active_time_ms, trial_metrics


def now_ms() -> int:
    return time.time_ns() // 1_000_000


class ExperimentService:
    def __init__(self, database: Database, clock=now_ms):
        self.database = database
        self.settings = database.settings
        self.clock = clock
        self.catalog = build_catalog(self.settings.reference_date)
        with database.transaction() as connection:
            snapshot = connection.execute("SELECT * FROM dataset WHERE singleton = 1").fetchone()
        self.records = [TaskRecord.model_validate(row) for row in json.loads(snapshot["records"])]
        self.truth = {key: QueryResult.model_validate(value) for key, value in json.loads(snapshot["ground_truth"]).items()}
        self.manifest = json.loads(snapshot["manifest"])

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
        if trial["status"] == "active" and now >= trial["started_ms"] + self.settings.trial_limit_seconds * 1000:
            self._finish(connection, trial, "incomplete", "time_limit",
                         trial["started_ms"] + self.settings.trial_limit_seconds * 1000)

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
        return {**trial, "prompt": definition.prompt if trial["status"] != "pending" else None,
                "tci": definition.tci, "level": definition.level,
                "mode_available": Mode(trial["mode"]) in AVAILABLE_MODES,
                "deadline_ms": trial["started_ms"] + self.settings.trial_limit_seconds * 1000 if trial["started_ms"] is not None else None,
                "attempts": attempts,
                "next_event_sequence": next_event_sequence,
                "last_event_offset_ms": last_event_offset_ms,
                "metrics": trial_metrics(trial, attempts, active, self.settings.trial_limit_seconds * 1000, self.settings.attempt_limit)}

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
                        "INSERT INTO trials (id, session_id, position, block_index, mode, task_id) VALUES (?, ?, ?, ?, ?, ?)",
                        (str(uuid4()), session_id, position, block, mode, task_id))
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
            if Mode(trial["mode"]) not in AVAILABLE_MODES:
                raise DomainError("mode_unavailable", "Этот режим ожидает выбора и подключения системы интерпретации.", 409)
            preceding = connection.execute("SELECT * FROM trials WHERE session_id = ? AND position < ? ORDER BY position DESC",
                                           (trial["session_id"], trial["position"])).fetchall()
            for row in preceding:
                previous = dict(row)
                self._expire(connection, previous, self.clock())
                if previous["status"] not in ("correct", "incomplete"):
                    raise DomainError("trial_order", "Сначала завершите предыдущую пробу.", 409)
            session = self._session(connection, trial["session_id"])
            if preceding and preceding[0]["block_index"] != trial["block_index"] and session["kind"] == "experiment":
                previous = self._trial(connection, preceding[0]["id"])
                remaining = previous["ended_ms"] + self.settings.break_seconds * 1000 - self.clock()
                if remaining > 0:
                    raise DomainError("break_required", f"До следующего блока осталось {(remaining + 999) // 1000} с.", 409)
            connection.execute("UPDATE trials SET status = 'active', started_ms = ? WHERE id = ?", (self.clock(), trial_id))
            return self._trial_view(connection, self._trial(connection, trial_id))

    def preview(self, trial_id, query: Query):
        with self.database.transaction() as connection:
            trial = self._trial(connection, trial_id)
            self._expire(connection, trial, self.clock())
            if trial["status"] != "active":
                raise DomainError("trial_not_active", "Проба не активна.", 409)
            if trial["mode"] != "M1":
                raise DomainError("preview_not_available", "Пошаговый просмотр доступен только в M1.", 409)
            return execute(self.records, query)

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
            if Mode(trial["mode"]) not in AVAILABLE_MODES:
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
            if finished >= trial["started_ms"] + self.settings.trial_limit_seconds * 1000:
                correct = False
                self._expire(connection, trial, finished)
            elif correct:
                self._finish(connection, trial, "correct", "correct", finished)
            elif ordinal >= self.settings.attempt_limit:
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
                        raise DomainError("event_conflict", "event_id уже использован для другого события.", 409)
                    continue
                if event.sequence <= last_seq or event.offset_ms < last_offset or event.offset_ms > max_offset:
                    raise DomainError("event_order", "События должны идти по sequence и времени внутри пробы.", 409)
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
        return result

    def export_metrics(self, session_id):
        session = self.session(session_id)
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["participant", "kind", "trial_id", "mode", "task", "status", "reason",
                         "elapsed_ms", "Tcorrect_actual_ms", "Tfirst_ms", "Tuser_active_ms", "A1_actual", "attempts", "Nretry_actual",
                         "Tcorrect_analysis_ms", "A1_analysis", "Nretry_analysis"])
        for trial in session["trials"]:
            actual, analysis = trial["metrics"]["actual"], trial["metrics"]["analysis"]
            writer.writerow([session["participant_code"], session["kind"], trial["id"], trial["mode"], trial["task_id"], trial["status"], trial["end_reason"],
                             actual["elapsed_ms"], actual["Tcorrect_ms"], actual["Tfirst_ms"], actual["Tuser_active_ms"], actual["A1"], actual["attempts"], actual["Nretry"],
                             analysis["Tcorrect_ms"], analysis["A1"], analysis["Nretry"]])
        return "\ufeff" + stream.getvalue()

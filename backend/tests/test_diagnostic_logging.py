import json
import logging
from uuid import uuid4

import pytest

from app.contracts import Event, Filter, M3AttemptInput, Query, SessionCreate
from app.database import Database
from app.errors import DomainError
from app.llm import Interpretation, PROMPT_VERSION
from app.service import ExperimentService


class InvalidQueryInterpreter:
    model = "test/gemma"
    prompt_sha256 = "diagnostic-sha"
    temperature = 0.0

    def interpret(self, text):
        query = Query(filters=[Filter(field="status", operator="gt", value="Открыта")])
        raw = json.dumps(query.model_dump(mode="json"), ensure_ascii=False)
        return Interpretation(query, raw, self.model, self.model, "Google", None, PROMPT_VERSION, 7.0, 10, 4)


def first_mode(service, mode):
    for index in range(5):
        session = service.create_session(SessionCreate(participant_code=f"LOG{mode}{index}"))
        if session["trials"][0]["mode"] == mode:
            return session
    raise AssertionError(mode)


def test_logging_flags_are_enabled_but_not_part_of_experiment_protocol(settings):
    database = Database(settings)
    database.initialize()
    service = ExperimentService(database)
    assert settings.logging.llmRoutesLogging is True
    assert settings.logging.taskMiningLogging is True
    assert "logging" not in service.manifest["protocol"]


def test_m4_invalid_query_log_contains_generated_query_and_executor_error(settings, clock, caplog):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    database = Database(settings)
    database.initialize()
    service = ExperimentService(database, clock)
    service.configure_m3(InvalidQueryInterpreter())
    session = first_mode(service, "M4")
    trial = service.start_trial(session["trials"][0]["id"])

    with pytest.raises(DomainError) as caught:
        service.preview_m4(trial["id"], M3AttemptInput(request_id=uuid4(), text="голосовая расшифровка"))

    assert caught.value.code == "llm_invalid_query"
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "[llm-routes]" in messages
    assert '"stage": "invalid_query"' in messages
    assert '"validation_code": "invalid_operator"' in messages
    assert '"mode": "M4"' in messages
    assert '"field": "status"' in messages


def test_task_mining_409_log_contains_server_and_incoming_cursors(service, clock, caplog):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    session = service.create_session(SessionCreate(participant_code="LOGEVENTS"))
    trial = service.start_trial(session["trials"][0]["id"])
    clock.advance(2000)
    service.ingest_events(trial["id"], [Event(event_id=uuid4(), sequence=0, offset_ms=1000, kind="input")])

    with pytest.raises(DomainError) as caught:
        service.ingest_events(trial["id"], [Event(event_id=uuid4(), sequence=0, offset_ms=1200, kind="input")])

    assert caught.value.code == "event_sequence"
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "[task-mining]" in messages
    assert '"stage": "rejected"' in messages
    assert '"code": "event_sequence"' in messages
    assert '"server_last_sequence": 0' in messages
    assert '"sequence": 0' in messages

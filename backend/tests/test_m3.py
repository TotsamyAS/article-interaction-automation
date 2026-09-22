import json
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.access import Principal, Signatures, require_principal
from app.api import create_app
from app.catalog import build_catalog
from app.contracts import M3AttemptInput, Query, SessionCreate
from app.database import Database
from app.errors import DomainError
from app.llm import (Interpretation, LLMError, OpenAICompatibleInterpreter,
                     PROMPT_VERSION, build_system_prompt, query_response_schema)
from app.service import ExperimentService


class FakeInterpreter:
    model = "test/tiny-model"
    prompt_sha256 = "test-prompt-sha256"
    temperature = 0.0

    def __init__(self, query=None, error=None):
        self.query = query
        self.error = error
        self.calls = 0

    def interpret(self, text):
        self.calls += 1
        if self.error:
            raise self.error
        raw = json.dumps(self.query.model_dump(mode="json"), ensure_ascii=False)
        return Interpretation(self.query, raw, self.model, self.model, "OpenAI", "fp-test", PROMPT_VERSION, 12.5, 111, 37)


def m3_service(settings, clock, interpreter):
    database = Database(settings)
    database.initialize()
    service = ExperimentService(database, clock, interpreter)
    service.create_session(SessionCreate(participant_code="P0"))
    service.create_session(SessionCreate(participant_code="P1"))
    session = service.create_session(SessionCreate(participant_code="P2"))
    assert session["trials"][0]["mode"] == "M3"
    trial = service.start_trial(session["trials"][0]["id"])
    return service, session, trial


def test_prompt_is_domain_compiler_and_schema_is_strict(settings):
    database = Database(settings)
    database.initialize()
    service = ExperimentService(database)
    prompt = build_system_prompt(service.records, settings.reference_date)
    assert settings.reference_date.isoformat() in prompt
    assert "Платежи" in prompt and "рефакторинг" in prompt
    assert "Не отвечай на задачу сам" in prompt
    assert "C1a" not in prompt and "C3e" not in prompt
    schema = query_response_schema()
    assert set(schema["required"]) == set(schema["properties"])
    assert set(schema["$defs"]["Filter"]["required"]) == set(schema["$defs"]["Filter"]["properties"])
    assert schema["additionalProperties"] is False


def test_openai_compatible_request_uses_strict_schema_and_small_model(settings, monkeypatch):
    database = Database(settings)
    database.initialize()
    service = ExperimentService(database)
    query = Query()
    response_body = json.dumps({
        "model": settings.llm_model,
        "choices": [{"message": {"content": json.dumps(query.model_dump(mode="json"))}}],
        "system_fingerprint": "fp-live-test",
        "openrouter_metadata": {"endpoints": {"available": [{"provider": "OpenAI", "selected": True}]}},
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }).encode()
    captured = {}

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return response_body

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    interpreter = OpenAICompatibleInterpreter(
        base_url=settings.llm_base_url, api_key="not-a-real-secret", model=settings.llm_model,
        temperature=settings.llm_temperature, timeout_seconds=settings.llm_timeout_seconds,
        records=service.records, reference_date=settings.reference_date,
    )
    result = interpreter.interpret("Покажи задачи")
    assert result.query == query
    assert result.provider == "OpenAI"
    assert result.system_fingerprint == "fp-live-test"
    assert captured["url"].endswith("/chat/completions")
    assert captured["payload"]["model"] == "openai/gpt-4.1-nano"
    assert captured["payload"]["temperature"] == 0
    assert captured["payload"]["seed"] == 0
    assert captured["payload"]["provider"] == {"require_parameters": True}
    assert captured["headers"]["X-openrouter-metadata"] == "enabled"
    assert captured["payload"]["response_format"]["type"] == "json_schema"
    assert captured["payload"]["response_format"]["json_schema"]["strict"] is True


def test_invalid_structured_response_keeps_diagnostics(settings, monkeypatch):
    database = Database(settings)
    database.initialize()
    service = ExperimentService(database)
    response_body = json.dumps({
        "model": settings.llm_model,
        "choices": [{"message": {"content": "{not-json"}}],
        "system_fingerprint": "fp-bad",
        "openrouter_metadata": {"endpoints": {"available": [{"provider": "Azure", "selected": True}]}},
        "usage": {"prompt_tokens": 20, "completion_tokens": 3},
    }).encode()

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return response_body

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: Response())
    interpreter = OpenAICompatibleInterpreter(
        base_url=settings.llm_base_url, api_key="not-a-real-secret", model=settings.llm_model,
        temperature=settings.llm_temperature, timeout_seconds=settings.llm_timeout_seconds,
        records=service.records, reference_date=settings.reference_date,
    )
    with pytest.raises(LLMError) as caught:
        interpreter.interpret("Покажи задачи")
    error = caught.value
    assert error.code == "llm_invalid_response"
    assert error.raw_response == "{not-json"
    assert error.provider == "Azure"
    assert error.system_fingerprint == "fp-bad"
    assert error.input_tokens == 20 and error.output_tokens == 3


def test_m3_runs_llm_query_through_common_executor_and_is_idempotent(settings, clock):
    query = build_catalog(settings.reference_date)["C1e"].query
    interpreter = FakeInterpreter(query)
    service, session, trial = m3_service(settings, clock, interpreter)
    request = M3AttemptInput(request_id=uuid4(), text="Покажи нужные задачи")
    first = service.submit_m3(trial["id"], request)
    repeated = service.submit_m3(trial["id"], request)
    assert first == repeated
    assert first["correct"]
    assert interpreter.calls == 1
    saved = service.export_session(session["id"])
    assert len(saved["attempt_log"]) == 1
    assert len(saved["interpretation_log"]) == 1
    interpretation = saved["interpretation_log"][0]
    assert interpretation["status"] == "ok"
    assert interpretation["query"] == query.model_dump(mode="json")
    assert interpretation["llm_ms"] == 12.5
    assert interpretation["input_tokens"] == 111
    assert interpretation["output_tokens"] == 37
    assert interpretation["provider"] == "OpenAI"
    assert interpretation["system_fingerprint"] == "fp-test"
    assert interpretation["prompt_sha256"] == "test-prompt-sha256"
    assert interpretation["temperature"] == 0.0


def test_m3_configuration_cannot_change_after_collection_starts(settings, clock):
    interpreter = FakeInterpreter(Query())
    service, _, trial = m3_service(settings, clock, interpreter)
    first = M3AttemptInput(request_id=uuid4(), text="Первый запрос")
    assert service.submit_m3(trial["id"], first)["correct"] is False
    assert interpreter.calls == 1

    interpreter.model = "test/other-model"
    with pytest.raises(DomainError) as caught:
        service.submit_m3(trial["id"], M3AttemptInput(request_id=uuid4(), text="Второй запрос"))
    assert caught.value.code == "m3_protocol_changed"
    assert interpreter.calls == 1


def test_m3_provider_failure_is_logged_without_consuming_attempt(settings, clock):
    interpreter = FakeInterpreter(error=LLMError("llm_unavailable", "LLM временно недоступен.", 503, 7.25))
    service, session, trial = m3_service(settings, clock, interpreter)
    request = M3AttemptInput(request_id=uuid4(), text="Покажи нужные задачи")
    with pytest.raises(DomainError, match="недоступен"):
        service.submit_m3(trial["id"], request)
    assert service.trial(trial["id"])["attempts"] == []
    saved = service.export_session(session["id"])
    assert saved["interpretation_log"][0]["status"] == "provider_error"
    assert saved["interpretation_log"][0]["llm_ms"] == 7.25
    with pytest.raises(DomainError):
        service.submit_m3(trial["id"], request)
    assert interpreter.calls == 1


def test_m3_api_is_available_when_interpreter_is_configured(settings, clock):
    query = build_catalog(settings.reference_date)["C1e"].query
    interpreter = FakeInterpreter(query)
    app = create_app(settings, clock, signatures_factory=lambda: Mock(spec=Signatures),
                     m3_interpreter_factory=lambda *_: interpreter)
    app.dependency_overrides[require_principal] = lambda: Principal("researcher", "R", "researcher", 1)
    with TestClient(app) as client:
        modes = {item["id"]: item["available"] for item in client.get("/api/protocol").json()["modes"]}
        assert modes["M3"] is True and modes["M4"] is False and modes["M5"] is False
        client.post("/api/sessions", json={"participant_code": "P0"})
        client.post("/api/sessions", json={"participant_code": "P1"})
        session = client.post("/api/sessions", json={"participant_code": "P2"}).json()
        trial = session["trials"][0]
        assert client.post(f'/api/trials/{trial["id"]}/start').status_code == 200
        response = client.post(f'/api/trials/{trial["id"]}/m3-attempts', json={"request_id": str(uuid4()), "text": "Покажи нужные задачи"})
        assert response.status_code == 200, response.json()
        assert response.json()["correct"] is True
        analytics = client.get("/api/analytics?mode=M3").json()
        assert len(analytics["interpretations"]) == 1
        assert analytics["interpretations"][0]["status"] == "ok"

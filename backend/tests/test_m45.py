import json
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient

from app.access import Principal, Signatures, require_principal
from app.agent import AgentExecution, AgentState, RouterAIAgent
from app.api import create_app
from app.catalog import build_catalog
from app.contracts import M3AttemptInput, M5AttemptInput, Query, SessionCreate
from app.database import Database
from app.errors import DomainError
from app.llm import Interpretation, PROMPT_VERSION
from app.service import ExperimentService


class FakeInterpreter:
    model = "test/gemma"
    prompt_sha256 = "m34-sha"
    temperature = 0.0

    def __init__(self, query):
        self.query = query

    def interpret(self, text):
        raw = json.dumps(self.query.model_dump(mode="json"), ensure_ascii=False)
        return Interpretation(self.query, raw, self.model, self.model, "Google", None, PROMPT_VERSION, 10.0, 20, 5)


class FakeAgent:
    model = "test/gemma"
    prompt_sha256 = "m5-sha"
    temperature = 0.0
    max_steps = 12

    def __init__(self, query):
        self.query = query
        self.calls = 0

    def run(self, text):
        self.calls += 1
        return AgentExecution(
            query=self.query,
            trajectory=[{"step": 1, "type": "tool", "tool": "search_tasks", "args": {}, "result": {"ok": True}}],
            requested_model=self.model,
            response_model=self.model,
            provider="Google",
            prompt_version="m5-agent-v1",
            prompt_sha256=self.prompt_sha256,
            llm_ms=100.0,
            tool_ms=1.0,
            total_ms=101.0,
            llm_calls=2,
            tool_calls=3,
            input_tokens=100,
            output_tokens=20,
            final_text="Готово",
            termination="model_finished",
        )


def session_with_first_mode(service, participant_prefix, target):
    for index in range(5):
        session = service.create_session(SessionCreate(participant_code=f"{participant_prefix}{index}"))
        if session["trials"][0]["mode"] == target:
            return session
    raise AssertionError(target)


def test_m4_reuses_m3_compiler_and_common_executor(settings, clock):
    database = Database(settings); database.initialize()
    service = ExperimentService(database, clock)
    session = session_with_first_mode(service, "M4P", "M4")
    query = build_catalog(settings.reference_date)[session["trials"][0]["task_id"]].query
    interpreter = FakeInterpreter(query)
    service.configure_m3(interpreter)
    trial = service.start_trial(session["trials"][0]["id"])
    result = service.submit_m4(trial["id"], M3AttemptInput(request_id=uuid4(), text="голосовая расшифровка"))
    assert result["correct"] is True
    exported = service.export_session(session["id"])
    assert exported["interpretation_log"][0]["user_text"] == "голосовая расшифровка"


def test_m5_agent_result_is_verified_and_trajectory_is_exported(settings, clock):
    database = Database(settings); database.initialize()
    service = ExperimentService(database, clock)
    session = session_with_first_mode(service, "M5P", "M5")
    query = build_catalog(settings.reference_date)[session["trials"][0]["task_id"]].query
    agent = FakeAgent(query)
    service.configure_m5(agent)
    trial = service.start_trial(session["trials"][0]["id"])
    request = M5AttemptInput(request_id=uuid4(), text="Покажи нужные задачи")
    first = service.submit_m5(trial["id"], request)
    repeated = service.submit_m5(trial["id"], request)
    assert first == repeated and first["correct"] is True
    assert agent.calls == 1
    exported = service.export_session(session["id"])
    run = exported["agent_log"][0]
    assert run["status"] == "ok" and run["tool_calls"] == 3
    assert run["trajectory"][0]["tool"] == "search_tasks"


def test_m5_model_can_change_but_agent_protocol_stays_locked(settings, clock):
    database = Database(settings); database.initialize()
    service = ExperimentService(database, clock)
    session = session_with_first_mode(service, "M5MODEL", "M5")
    agent = FakeAgent(Query())
    service.configure_m5(agent)
    trial = service.start_trial(session["trials"][0]["id"])

    assert service.submit_m5(trial["id"], M5AttemptInput(request_id=uuid4(), text="Первый запрос"))["correct"] is False
    agent.model = "test/other-model"
    assert service.submit_m5(trial["id"], M5AttemptInput(request_id=uuid4(), text="Второй запрос"))["correct"] is False
    assert agent.calls == 2

    agent.max_steps = 13
    try:
        service.submit_m5(trial["id"], M5AttemptInput(request_id=uuid4(), text="Третий запрос"))
    except DomainError as error:
        assert error.code == "m5_protocol_changed"
    else:
        raise AssertionError("M5 protocol change should remain blocked")
    assert agent.calls == 2


def test_agent_state_rejects_string_for_in_without_changing_selection(settings):
    database = Database(settings); database.initialize(); service = ExperimentService(database)
    state = AgentState(service.records)
    bad = state.execute_tool("filter_by_field", {"field": "status", "operator": "in", "value": "Открыта, В работе"})
    assert bad["ok"] is False and bad["state_unchanged"] is True
    assert state.snapshot()["record_count"] == 120


def test_routerai_agent_loop_uses_notebook_tool_contract(settings, monkeypatch):
    database = Database(settings); database.initialize(); service = ExperimentService(database)
    responses = [
        {
            "model": settings.llm_model,
            "provider": "Google",
            "choices": [{"message": {"content": None, "tool_calls": [
                {"id": "1", "type": "function", "function": {"name": "search_tasks", "arguments": "{}"}},
                {"id": "2", "type": "function", "function": {"name": "filter_by_field", "arguments": json.dumps({"field": "status", "operator": "eq", "value": "В работе"})}},
                {"id": "3", "type": "function", "function": {"name": "filter_by_field", "arguments": json.dumps({"field": "priority", "operator": "eq", "value": "Средний"})}},
            ]}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
        {"model": settings.llm_model, "provider": "Google", "choices": [{"message": {"content": "Готово"}}],
         "usage": {"prompt_tokens": 20, "completion_tokens": 2}},
    ]
    captured = []

    class Response:
        def __init__(self, payload): self.payload = payload
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return json.dumps(self.payload, ensure_ascii=False).encode()

    def fake_urlopen(request, timeout):
        captured.append(json.loads(request.data))
        return Response(responses.pop(0))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    agent = RouterAIAgent(base_url=settings.llm_base_url, api_key="secret", model=settings.llm_model,
                          temperature=0, timeout_seconds=60, max_steps=12,
                          records=service.records, reference_date=settings.reference_date)
    execution = agent.run("Показать задачи со статусом «В работе» и приоритетом «Средний».")
    expected = build_catalog(settings.reference_date)["C1e"].query
    assert execution.query.filters == expected.filters
    assert execution.tool_calls == 3 and execution.llm_calls == 2
    assert captured[0]["model"] == settings.llm_model
    assert captured[0]["tool_choice"] == "auto" and len(captured[0]["tools"]) == 7
    assert "parallel_tool_calls" not in captured[0]


def test_api_exposes_all_modes_when_both_routerai_components_are_configured(settings, clock):
    query = build_catalog(settings.reference_date)["C1e"].query
    interpreter, agent = FakeInterpreter(query), FakeAgent(query)
    app = create_app(settings, clock, signatures_factory=lambda: Mock(spec=Signatures),
                     m3_interpreter_factory=lambda *_: interpreter, m5_agent_factory=lambda *_: agent)
    app.dependency_overrides[require_principal] = lambda: Principal("researcher", "R", "researcher", 1)
    with TestClient(app) as client:
        modes = {item["id"]: item["available"] for item in client.get("/api/protocol").json()["modes"]}
        assert modes == {"M1": True, "M2": True, "M3": True, "M4": True, "M5": True}

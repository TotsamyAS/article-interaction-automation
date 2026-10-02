from app.access import Principal, require_principal


def test_admin_validation_lists_all_tasks_without_creating_sessions(client):
    client.app.dependency_overrides[require_principal] = lambda: Principal("admin-id", "admin", "participant", 1)
    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["validation_mode"] is True
    assert me.json()["sessions"] == []

    response = client.get("/api/validation/tasks")
    assert response.status_code == 200
    tasks = response.json()
    assert {task["id"] for task in tasks if not task["training"]} == {
        *(f"C1{x}" for x in "abcde"), *(f"C2{x}" for x in "abcde"), *(f"C3{x}" for x in "abcde")
    }
    assert all("query" in task and "result" in task for task in tasks)

    with client.app.state.service.database.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM sessions WHERE participant_code='admin'").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_admin_invitation_cannot_create_experiment_session(client):
    client.app.dependency_overrides[require_principal] = lambda: Principal("admin-id", "admin", "participant", 1)
    response = client.post("/api/sessions", json={"participant_code": "admin", "kind": "experiment"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "validation_mode"


def test_regular_participant_cannot_open_validation_catalog(client):
    client.app.dependency_overrides[require_principal] = lambda: Principal("p-id", "P001", "participant", 1)
    assert client.get("/api/validation/tasks").status_code == 403


def test_admin_manual_preview_and_submit_use_real_executor_without_telemetry(client):
    from uuid import uuid4

    client.app.dependency_overrides[require_principal] = lambda: Principal("admin-id", "admin", "participant", 1)
    task = next(item for item in client.get("/api/validation/tasks").json() if item["id"] == "C1a")
    preview = client.post("/api/validation/tasks/C1a/preview", json=task["query"])
    final = client.post(
        "/api/validation/tasks/C1a/attempts",
        json={"request_id": str(uuid4()), "query": task["query"]},
    )
    assert preview.status_code == 200
    assert final.status_code == 200 and final.json()["correct"] is True
    assert final.json()["export_url"] is None

    with client.app.state.service.database.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM trials").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_admin_m3_m4_m5_use_live_pipelines_but_never_write_experiment_logs(settings, clock):
    import json
    from unittest.mock import Mock
    from uuid import uuid4

    from fastapi.testclient import TestClient

    from app.access import Signatures
    from app.agent import AgentExecution
    from app.api import create_app
    from app.asr import ASRResult
    from app.catalog import build_catalog
    from app.llm import Interpretation, PROMPT_VERSION

    query = build_catalog(settings.reference_date)["C1a"].query

    class Interpreter:
        model = "test/admin-m3"
        prompt_sha256 = "admin-m3-sha"
        temperature = 0.0

        def __init__(self): self.calls = 0
        def interpret(self, text):
            self.calls += 1
            return Interpretation(
                query,
                json.dumps(query.model_dump(mode="json"), ensure_ascii=False),
                self.model,
                self.model,
                "test",
                None,
                PROMPT_VERSION,
                5.0,
                10,
                5,
            )

    class Agent:
        model = "test/admin-m5"
        prompt_sha256 = "admin-m5-sha"
        temperature = 0.0
        max_steps = 12

        def __init__(self): self.calls = 0
        def run(self, text):
            self.calls += 1
            return AgentExecution(
                query=query,
                trajectory=[{"step": 1, "type": "tool", "tool": "search_tasks", "args": {}, "result": {"ok": True}}],
                requested_model=self.model,
                response_model=self.model,
                provider="test",
                prompt_version="m5-agent-v1",
                prompt_sha256=self.prompt_sha256,
                llm_ms=5.0,
                tool_ms=1.0,
                total_ms=6.0,
                llm_calls=1,
                tool_calls=1,
                input_tokens=10,
                output_tokens=5,
                final_text="Готово",
                termination="model_finished",
            )

    class ASR:
        protocol = {"engine": "routerai", "provider": "routerai", "model": settings.asr_model, "language": "ru"}
        def __init__(self): self.calls = 0
        def transcribe(self, audio, mime_type):
            self.calls += 1
            assert audio == b"admin-audio"
            return ASRResult("Покажи задачи", "ru", None, 7.0)

    interpreter, agent, asr = Interpreter(), Agent(), ASR()
    app = create_app(
        settings,
        clock,
        signatures_factory=lambda: Mock(spec=Signatures),
        m3_interpreter_factory=lambda *_: interpreter,
        m5_agent_factory=lambda *_: agent,
        asr_factory=lambda _: asr,
    )
    app.dependency_overrides[require_principal] = lambda: Principal("admin-id", "admin", "participant", 1)

    with TestClient(app) as validation_client:
        for mode in ("m3", "m5"):
            request_id = str(uuid4())
            payload = {"request_id": request_id, "text": "Покажи задачи"}
            preview = validation_client.post(f"/api/validation/tasks/C1a/{mode}-preview", json=payload)
            final = validation_client.post(f"/api/validation/tasks/C1a/{mode}-attempts", json=payload)
            assert preview.status_code == 200, preview.json()
            assert final.status_code == 200 and final.json()["correct"] is True

        m4_request_id = str(uuid4())
        transcript = validation_client.post(
            f"/api/validation/tasks/C1a/m4-transcribe?request_id={m4_request_id}&duration_ms=1000",
            content=b"admin-audio",
            headers={"Content-Type": "audio/webm"},
        )
        assert transcript.status_code == 200
        m4_payload = {"request_id": m4_request_id, "text": transcript.json()["text"]}
        assert validation_client.post("/api/validation/tasks/C1a/m4-preview", json=m4_payload).status_code == 200
        m4_final = validation_client.post("/api/validation/tasks/C1a/m4-attempts", json=m4_payload)
        assert m4_final.status_code == 200 and m4_final.json()["correct"] is True

        assert interpreter.calls == 2  # one M3 preview + one M4 preview; final submits are cached
        assert agent.calls == 1        # M5 final submit reuses its preview
        assert asr.calls == 1

        with app.state.service.database.transaction() as connection:
            for table in ("sessions", "trials", "attempts", "events", "m3_interpretations", "m4_transcriptions", "m5_agent_runs"):
                assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_regular_participant_cannot_use_validation_execution_endpoints(client):
    from uuid import uuid4

    client.app.dependency_overrides[require_principal] = lambda: Principal("p-id", "P001", "participant", 1)
    response = client.post(
        "/api/validation/tasks/C1a/attempts",
        json={"request_id": str(uuid4()), "query": {}},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "validation_denied"

import json
import logging
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient

from app.access import Principal, Signatures, require_principal
from app.api import create_app
from app.asr import ASRResult, RouterAIASR
from app.contracts import SessionCreate


class FakeASR:
    protocol = {
        "engine": "routerai",
        "engine_version": "audio-transcriptions-v1",
        "provider": "routerai",
        "model": "nvidia/nemotron-3.5-asr-streaming-multilingual-0.6b",
        "language": "ru",
        "audio_limit_seconds": 20,
    }

    def __init__(self):
        self.calls = 0

    def transcribe(self, audio, mime_type):
        self.calls += 1
        assert audio == b"fake-webm"
        assert mime_type == "audio/webm"
        return ASRResult("Показать открытые задачи", "ru", None, 123.5)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def first_m4(service):
    for index in range(5):
        session = service.create_session(SessionCreate(participant_code=f"ASR{index}"))
        if session["trials"][0]["mode"] == "M4":
            return session
    raise AssertionError("M4 not scheduled first")


def test_m4_audio_is_transcribed_idempotently_and_exported(settings, clock):
    fake_asr = FakeASR()
    app = create_app(
        settings,
        clock,
        signatures_factory=lambda: Mock(spec=Signatures),
        m3_interpreter_factory=lambda *_: object(),
        asr_factory=lambda _: fake_asr,
    )
    app.dependency_overrides[require_principal] = lambda: Principal("researcher", "R", "researcher", 1)
    with TestClient(app) as client:
        session = first_m4(app.state.service)
        trial = app.state.service.start_trial(session["trials"][0]["id"])
        request_id = uuid4()
        url = f"/api/trials/{trial['id']}/m4-transcribe?request_id={request_id}&duration_ms=1200"
        first = client.post(url, content=b"fake-webm", headers={"Content-Type": "audio/webm"})
        repeated = client.post(url, content=b"different", headers={"Content-Type": "audio/webm"})
        assert first.status_code == 200
        assert repeated.status_code == 200
        assert first.json() == repeated.json()
        assert first.json()["text"] == "Показать открытые задачи"
        assert first.json()["model"] == settings.asr_model
        assert fake_asr.calls == 1
        exported = app.state.service.export_session(session["id"])
        assert len(exported["m4_transcription_log"]) == 1
        row = exported["m4_transcription_log"][0]
        assert row["audio_bytes"] == len(b"fake-webm")
        assert row["audio_duration_ms"] == 1200
        assert row["transcript"] == "Показать открытые задачи"
        assert row["compute_type"] == "routerai-api"
        assert exported["attempt_log"] == []
        analytics = client.get("/api/analytics").json()
        assert analytics["protocol"]["manifest"]["m4_asr"]["model"] == settings.asr_model
        assert analytics["protocol"]["manifest"]["m4_asr"]["provider"] == "routerai"
        assert len(analytics["m4_transcriptions"]) == 1
        assert analytics["m4_transcriptions"][0]["Tasr_ms"] == 123.5


def test_m4_transcribe_rejects_unsupported_audio_before_asr(settings, clock):
    fake_asr = FakeASR()
    app = create_app(
        settings,
        clock,
        signatures_factory=lambda: Mock(spec=Signatures),
        m3_interpreter_factory=lambda *_: object(),
        asr_factory=lambda _: fake_asr,
    )
    app.dependency_overrides[require_principal] = lambda: Principal("researcher", "R", "researcher", 1)
    with TestClient(app) as client:
        session = first_m4(app.state.service)
        trial = app.state.service.start_trial(session["trials"][0]["id"])
        response = client.post(
            f"/api/trials/{trial['id']}/m4-transcribe?request_id={uuid4()}&duration_ms=1000",
            content=b"not-audio",
            headers={"Content-Type": "text/plain"},
        )
        assert response.status_code == 415
        assert response.json()["error"]["code"] == "asr_audio_type"
        assert fake_asr.calls == 0


def test_internal_asr_ready_reports_routerai_protocol(settings, clock):
    fake_asr = FakeASR()
    app = create_app(
        settings,
        clock,
        signatures_factory=lambda: Mock(spec=Signatures),
        m3_interpreter_factory=lambda *_: None,
        asr_factory=lambda _: fake_asr,
    )
    with TestClient(app) as client:
        response = client.get("/internal/asr-ready")
        assert response.status_code == 200
        assert response.json()["protocol"]["engine"] == "routerai"
        assert response.json()["protocol"]["model"] == settings.asr_model


def test_routerai_asr_uses_configured_model_and_same_audio_endpoint(settings, caplog):
    calls = []

    def opener(request, timeout):
        calls.append((request, timeout))
        return FakeResponse({"text": "Проверка распознавания.", "language": "ru"})

    configured = settings.model_copy(update={"asr_model": "qwen/qwen3-asr-0.6b"})
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    asr = RouterAIASR(configured, opener=opener, api_key="test-key")
    result = asr.transcribe(b"fake-webm", "audio/webm;codecs=opus")

    assert result.text == "Проверка распознавания."
    assert result.detected_language == "ru"
    assert len(calls) == 1
    request, timeout = calls[0]
    assert request.full_url == f"{settings.llm_base_url}/audio/transcriptions"
    assert timeout == settings.asr_timeout_seconds
    assert request.headers["Authorization"] == "Bearer test-key"
    content_type = request.headers["Content-type"]
    assert content_type.startswith("multipart/form-data; boundary=")
    assert b'name="model"' in request.data
    assert b"qwen/qwen3-asr-0.6b" in request.data
    assert b'name="language"' in request.data
    assert b"ru" in request.data
    assert b'filename="speech.webm"' in request.data
    assert b"fake-webm" in request.data
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert '"stage": "transcribe_success"' in messages
    assert '"model": "qwen/qwen3-asr-0.6b"' in messages

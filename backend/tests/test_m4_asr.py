import logging
from unittest.mock import Mock

import pytest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.access import Principal, Signatures, require_principal
from app.api import create_app
from app.asr import ASRError, ASRResult, GigaAMASR
from app.contracts import SessionCreate


class FakeASR:
    protocol = {
        "engine": "gigaam-v3",
        "engine_version": "3",
        "model": "ai-sage/GigaAM-v3",
        "revision": "e2e_rnnt",
        "device": "cpu",
        "compute_type": "float32",
        "language": "ru",
        "audio_limit_seconds": 20,
    }

    def __init__(self):
        self.calls = 0
        self.loaded = False

    def ensure_loaded(self):
        self.loaded = True
        return object()

    def transcribe(self, audio, mime_type):
        self.calls += 1
        assert audio == b"fake-webm"
        assert mime_type == "audio/webm"
        return ASRResult("Показать открытые задачи", "ru", 0.99, 123.5)


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
        assert exported["attempt_log"] == []
        analytics = client.get("/api/analytics").json()
        assert analytics["protocol"]["manifest"]["m4_asr"]["model"] == "ai-sage/GigaAM-v3"
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


def test_internal_asr_ready_warms_long_lived_instance(settings, clock):
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
        assert response.json()["protocol"]["model"] == "ai-sage/GigaAM-v3"
        assert fake_asr.loaded is True


def test_missing_baked_model_is_a_controlled_logged_error(settings, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    configured = settings.model_copy(update={"asr_model_path": str(tmp_path / "missing-model")})
    asr = GigaAMASR(configured)

    with pytest.raises(ASRError) as caught:
        asr.ensure_loaded()

    assert "backend image" in str(caught.value)
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "[asr]" in messages
    assert '"stage": "model_files_missing"' in messages
    assert "config.json" in messages

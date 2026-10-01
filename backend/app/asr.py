from __future__ import annotations

import json
import logging
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Settings


LOGGER = logging.getLogger("uvicorn.error")

ASR_ENGINE = "gigaam-v3"
ASR_ENGINE_VERSION = "3"


class ASRError(RuntimeError):
    pass


@dataclass(frozen=True)
class ASRResult:
    text: str
    detected_language: str | None
    language_probability: float | None
    asr_ms: float


class GigaAMASR:
    """CPU-only GigaAM-v3 adapter for short Russian M4 utterances.

    Model files are baked into the backend image during ``docker build``. Runtime
    loading is strictly local: participant requests never contact Hugging Face.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._model = None
        self._load_lock = threading.Lock()
        self._transcribe_lock = threading.Lock()

    def _log(self, stage: str, **fields) -> None:
        if not self.settings.logging.asrLogging:
            return
        LOGGER.info(
            "[asr] %s",
            json.dumps({"stage": stage, **fields}, ensure_ascii=False, sort_keys=True, default=str),
        )

    @property
    def protocol(self) -> dict:
        return {
            "engine": ASR_ENGINE,
            "engine_version": ASR_ENGINE_VERSION,
            "model": self.settings.asr_model,
            "revision": self.settings.asr_revision,
            "device": self.settings.asr_device,
            "compute_type": self.settings.asr_compute_type,
            "language": self.settings.asr_language,
            "audio_limit_seconds": self.settings.asr_max_audio_seconds,
        }

    def _model_path(self) -> Path:
        path = Path(self.settings.asr_model_path)
        required = ("config.json", "modeling_gigaam.py", "pytorch_model.bin", "tokenizer.model")
        missing = [name for name in required if not (path / name).is_file()]
        if missing:
            self._log("model_files_missing", path=str(path), missing=missing)
            raise ASRError(
                "GigaAM-v3 отсутствует внутри backend image. Пересоберите образ; "
                f"не найдены: {', '.join(missing)}."
            )
        return path

    def ensure_loaded(self):
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is not None:
                return self._model
            model_path = self._model_path()
            self._log(
                "model_load_start",
                model=self.settings.asr_model,
                revision=self.settings.asr_revision,
                path=str(model_path),
                device=self.settings.asr_device,
            )
            try:
                import torch
                from transformers import AutoModel

                model = AutoModel.from_pretrained(
                    str(model_path),
                    trust_remote_code=True,
                    local_files_only=True,
                    torch_dtype=torch.float32,
                )
                model.eval()
                self._model = model
            except Exception as exc:
                self._log(
                    "model_load_failed",
                    model=self.settings.asr_model,
                    revision=self.settings.asr_revision,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                raise ASRError(f"Не удалось загрузить локальную GigaAM-v3: {exc}") from exc
            self._log(
                "model_load_ready",
                model=self.settings.asr_model,
                revision=self.settings.asr_revision,
                path=str(model_path),
            )
            return self._model

    @staticmethod
    def _suffix(mime_type: str) -> str:
        base = mime_type.split(";", 1)[0].strip().lower()
        return {
            "audio/webm": ".webm",
            "audio/ogg": ".ogg",
            "audio/mp4": ".m4a",
            "audio/x-m4a": ".m4a",
            "audio/wav": ".wav",
            "audio/wave": ".wav",
            "audio/x-wav": ".wav",
        }.get(base, ".audio")

    def transcribe(self, audio: bytes, mime_type: str) -> ASRResult:
        if not audio:
            raise ASRError("Получена пустая аудиозапись.")
        self._log(
            "transcribe_start",
            bytes=len(audio),
            mime_type=mime_type,
            model=self.settings.asr_model,
            revision=self.settings.asr_revision,
        )
        model = self.ensure_loaded()
        started = time.perf_counter_ns()
        try:
            with tempfile.NamedTemporaryFile(suffix=self._suffix(mime_type)) as handle:
                handle.write(audio)
                handle.flush()
                with self._transcribe_lock:
                    text = model.transcribe(handle.name).strip()
        except Exception as exc:
            self._log(
                "transcribe_failed",
                model=self.settings.asr_model,
                revision=self.settings.asr_revision,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise ASRError(f"GigaAM-v3 не смогла обработать аудио: {exc}") from exc
        elapsed = (time.perf_counter_ns() - started) / 1_000_000
        self._log(
            "transcribe_success",
            model=self.settings.asr_model,
            revision=self.settings.asr_revision,
            asr_ms=round(elapsed, 1),
            transcript=text,
        )
        return ASRResult(
            text=text,
            detected_language="ru",
            language_probability=None,
            asr_ms=elapsed,
        )

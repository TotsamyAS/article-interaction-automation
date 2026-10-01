from __future__ import annotations

import json
import logging
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import Settings


LOGGER = logging.getLogger("uvicorn.error")

ASR_ENGINE = "routerai"
ASR_ENGINE_VERSION = "audio-transcriptions-v1"
ASR_STORAGE_KIND = "routerai-api"


class ASRError(RuntimeError):
    pass


@dataclass(frozen=True)
class ASRResult:
    text: str
    detected_language: str | None
    language_probability: float | None
    asr_ms: float


class RouterAIASR:
    """Model-agnostic RouterAI /audio/transcriptions adapter for M4."""

    def __init__(self, settings: Settings, *, opener=None, api_key: str | None = None):
        self.settings = settings
        self._opener = opener or urllib.request.urlopen
        self._api_key = api_key

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
            "provider": self.settings.asr_provider,
            "model": self.settings.asr_model,
            "language": self.settings.asr_language,
            "audio_limit_seconds": self.settings.asr_max_audio_seconds,
        }

    def _key(self) -> str:
        key = (self._api_key or os.environ.get("ROUTERAI_API_KEY", "")).strip()
        if not key:
            raise ASRError("ROUTERAI_API_KEY не настроен для M4 ASR.")
        return key

    @staticmethod
    def _filename(mime_type: str) -> str:
        base = mime_type.split(";", 1)[0].strip().lower()
        suffixes = {
            "audio/webm": ".webm",
            "audio/ogg": ".ogg",
            "audio/mp4": ".m4a",
            "audio/x-m4a": ".m4a",
            "audio/wav": ".wav",
            "audio/wave": ".wav",
            "audio/x-wav": ".wav",
        }
        try:
            return f"speech{suffixes[base]}"
        except KeyError as exc:
            raise ASRError(f"Неподдерживаемый формат аудио для RouterAI ASR: {base or 'unknown'}.") from exc

    @staticmethod
    def _multipart(audio: bytes, mime_type: str, model: str, language: str) -> tuple[bytes, str]:
        boundary = f"----aia-asr-{time.time_ns()}"
        pieces = []
        for name, value in (("model", model), ("language", language)):
            pieces.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8")
            )
        filename = RouterAIASR._filename(mime_type)
        pieces.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {mime_type.split(';', 1)[0].strip()}\r\n\r\n".encode("utf-8")
        )
        pieces.extend((audio, f"\r\n--{boundary}--\r\n".encode("ascii")))
        return b"".join(pieces), boundary

    @staticmethod
    def _response_text(payload: Any) -> str:
        if isinstance(payload, dict):
            text = payload.get("text")
            if isinstance(text, str):
                return text.strip()
            data = payload.get("data")
            if isinstance(data, dict) and isinstance(data.get("text"), str):
                return data["text"].strip()
        return ""

    def transcribe(self, audio: bytes, mime_type: str) -> ASRResult:
        if not audio:
            raise ASRError("Получена пустая аудиозапись.")
        endpoint = f"{self.settings.llm_base_url.rstrip('/')}/audio/transcriptions"
        body, boundary = self._multipart(audio, mime_type, self.settings.asr_model, self.settings.asr_language)
        request = urllib.request.Request(
            endpoint,
            data=body,
            headers={
                "Authorization": f"Bearer {self._key()}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "application/json",
            },
            method="POST",
        )
        self._log(
            "transcribe_start",
            bytes=len(audio),
            mime_type=mime_type,
            provider=self.settings.asr_provider,
            model=self.settings.asr_model,
        )
        started = time.perf_counter_ns()
        try:
            with self._opener(request, timeout=self.settings.asr_timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            elapsed = (time.perf_counter_ns() - started) / 1_000_000
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:1200]
            except Exception:
                detail = ""
            self._log(
                "transcribe_http_error",
                model=self.settings.asr_model,
                status=exc.code,
                asr_ms=round(elapsed, 1),
                response=detail,
            )
            raise ASRError(f"RouterAI ASR вернул HTTP {exc.code}. Повторите запись.") from None
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            elapsed = (time.perf_counter_ns() - started) / 1_000_000
            self._log(
                "transcribe_unavailable",
                model=self.settings.asr_model,
                error_type=type(exc).__name__,
                error=str(exc),
                asr_ms=round(elapsed, 1),
            )
            raise ASRError("RouterAI ASR недоступен или не ответил вовремя. Повторите запись.") from None
        elapsed = (time.perf_counter_ns() - started) / 1_000_000
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self._log("transcribe_invalid_json", model=self.settings.asr_model, asr_ms=round(elapsed, 1), response=raw[:1200])
            raise ASRError("RouterAI ASR вернул некорректный JSON.") from None
        text = self._response_text(payload)
        if not text:
            self._log("transcribe_missing_text", model=self.settings.asr_model, asr_ms=round(elapsed, 1), response=raw[:1200])
            raise ASRError("RouterAI ASR не вернул распознанный текст.")
        detected_language = payload.get("language") if isinstance(payload, dict) and isinstance(payload.get("language"), str) else self.settings.asr_language
        language_probability = None
        if isinstance(payload, dict) and isinstance(payload.get("language_probability"), (int, float)):
            language_probability = float(payload["language_probability"])
        self._log(
            "transcribe_success",
            provider=self.settings.asr_provider,
            model=self.settings.asr_model,
            asr_ms=round(elapsed, 1),
            transcript=text,
        )
        return ASRResult(
            text=text,
            detected_language=detected_language,
            language_probability=language_probability,
            asr_ms=elapsed,
        )

from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    host: str
    port: int = Field(ge=1, le=65535)
    database_path: str
    dataset_version: str
    reference_date: date
    seed: int
    trial_limit_seconds: int = Field(gt=0)
    attempt_limit: int = Field(gt=0)
    break_seconds: int = Field(ge=0)
    idle_threshold_ms: int = Field(gt=0)
    average_tolerance: Decimal = Field(gt=0, allow_inf_nan=False)
    public_base_url: str
    login_redirect_path: str
    access_cookie_seconds: int = Field(ge=60)

    @model_validator(mode="after")
    def access_urls(self):
        parsed = urlsplit(self.public_base_url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
            raise ValueError("public_base_url должен содержать только схему и адрес сервера.")
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("Для внешнего адреса public_base_url требуется HTTPS.")
        if (not self.login_redirect_path.startswith("/") or self.login_redirect_path.startswith("//")
                or "\\" in self.login_redirect_path or any(ord(c) < 32 for c in self.login_redirect_path)):
            raise ValueError("login_redirect_path должен быть локальным путём.")
        return self


def load_settings() -> Settings:
    return Settings.model_validate_json(
        (Path(__file__).resolve().parent.parent / "config.json").read_text(encoding="utf-8")
    )

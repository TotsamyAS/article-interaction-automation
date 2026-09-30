"""Reusable invitations. Only runtime/user CLI handles credential values."""
import hashlib
import hmac
import os
import re
import time
from dataclasses import dataclass
from typing import Callable
from uuid import UUID, uuid4

from fastapi import Request

from .database import Database
from .errors import DomainError

COOKIE_NAME = "experiment_access"
REQUIRED_SECRET = "INVITATION_SIGNING_KEY"


def key_from_runtime(getenv: Callable = os.getenv) -> bytes:
    # Called by the application/CLI, never by development tools to inspect env.
    value = getenv(REQUIRED_SECRET)
    if not value:
        raise RuntimeError("Требуется INVITATION_SIGNING_KEY: создайте ключ локально и укажите его в корневом .env.")
    if len(value.encode()) < 32:
        raise RuntimeError("INVITATION_SIGNING_KEY должен содержать не менее 32 байт; обновите корневой .env.")
    return value.encode()


class Signatures:
    def __init__(self, key: bytes):
        self._key = key

    def sign(self, message: str) -> str:
        return hmac.new(self._key, message.encode("ascii"), hashlib.sha256).hexdigest()

    def matches(self, message: str, signature: str) -> bool:
        return hmac.compare_digest(self.sign(message), signature)


@dataclass(frozen=True)
class Principal:
    id: str
    code: str
    role: str
    generation: int


def denied(status=401):
    return DomainError("access_denied", "Войдите по действующей ссылке приглашения.", status)


class AccessService:
    def __init__(self, database: Database, signatures: Signatures, clock=time.time):
        self.database = database
        self.signatures = signatures
        self.clock = clock

    def provision(self, code: str, role: str) -> Principal:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", code) or role not in ("participant", "researcher"):
            raise DomainError("invalid_principal", "Нужны код из букв/цифр и роль participant/researcher.")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM access_principals WHERE code = ?", (code,)).fetchone()
            if row:
                if row["role"] != role or not row["enabled"]:
                    raise DomainError("principal_conflict", "Код уже связан с другой ролью или приглашение отозвано.", 409)
            else:
                connection.execute("INSERT INTO access_principals (id, code, role) VALUES (?, ?, ?)", (str(uuid4()), code, role))
                row = connection.execute("SELECT * FROM access_principals WHERE code = ?", (code,)).fetchone()
            return self._principal(row)

    @staticmethod
    def _principal(row):
        return Principal(row["id"], row["code"], row["role"], row["generation"])

    def _get(self, identifier: str, generation: int) -> Principal:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM access_principals WHERE id = ? AND enabled = 1 AND generation = ?",
                                     (identifier, generation)).fetchone()
        if row is None:
            raise denied()
        return self._principal(row)

    def invitation(self, principal: Principal) -> str:
        message = f"invite.{principal.id}.{principal.generation}"
        return message + "." + self.signatures.sign(message)

    def cookie(self, principal: Principal) -> str:
        expires = int(self.clock()) + self.database.settings.access_cookie_seconds
        message = f"session.{principal.id}.{principal.generation}.{expires}"
        return message + "." + self.signatures.sign(message)

    def validate(self, value: str, purpose: str) -> Principal:
        if len(value) > 256 or not value.isascii():
            raise denied()
        parts = value.split(".")
        if len(parts) != (4 if purpose == "invite" else 5) or parts[0] != purpose:
            raise denied()
        try:
            if str(UUID(parts[1])) != parts[1] or int(parts[2]) < 1:
                raise ValueError
            if purpose == "session" and int(parts[3]) <= self.clock():
                raise ValueError
        except (ValueError, OverflowError):
            raise denied() from None
        if not re.fullmatch(r"[0-9a-f]{64}", parts[-1]):
            raise denied()
        if not self.signatures.matches(".".join(parts[:-1]), parts[-1]):
            raise denied()
        return self._get(parts[1], int(parts[2]))

    def revoke(self, code: str):
        with self.database.transaction() as connection:
            cursor = connection.execute("UPDATE access_principals SET enabled = 0, generation = generation + 1 WHERE code = ?", (code,))
            if cursor.rowcount != 1:
                raise DomainError("principal_not_found", "Код не найден.", 404)

    def authorize_code(self, principal: Principal, code: str):
        if principal.role != "researcher" and principal.code != code:
            raise denied(403)

    @staticmethod
    def researcher(principal: Principal):
        if principal.role != "researcher":
            raise denied(403)

    def authorize_resource(self, principal: Principal, kind: str, identifier: str):
        # Allowlisted query shapes; callers cannot choose arbitrary table names.
        queries = {
            "session": "SELECT participant_code FROM sessions WHERE id = ?",
            "trial": "SELECT s.participant_code FROM trials t JOIN sessions s ON s.id = t.session_id WHERE t.id = ?",
            "attempt": "SELECT s.participant_code FROM attempts a JOIN trials t ON t.id = a.trial_id JOIN sessions s ON s.id = t.session_id WHERE a.id = ?",
        }
        with self.database.transaction() as connection:
            row = connection.execute(queries[kind], (identifier,)).fetchone()
        if row is None or (principal.role != "researcher" and row["participant_code"] != principal.code):
            raise DomainError("resource_not_found", "Ресурс не найден.", 404)


def require_principal(request: Request) -> Principal:
    context = request.headers.get('X-Access-Context') or request.query_params.get('access')
    if context and not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', context):
        raise denied()
    value = request.cookies.get(f'{COOKIE_NAME}_{context}') if context else None
    # Old clients/links retain their cookie; an explicit context must match its owner.
    value = value or request.cookies.get(COOKIE_NAME)
    if not value:
        raise denied()
    principal = request.app.state.access.validate(value, "session")
    if context and principal.id != context:
        raise denied()
    return principal


def same_origin(request: Request):
    # Reject cross-origin mutations before reading a potentially sensitive body.
    expected = request.app.state.service.settings.public_base_url.rstrip("/")
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site" or (origin and origin != expected):
        raise DomainError("origin_denied", "Запрос должен исходить с адреса приложения.", 403)

"""M3 natural-language -> Query interpreter over an OpenAI-compatible API."""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from pydantic import ValidationError

from .contracts import Query, TaskRecord

PROMPT_VERSION = "m3-query-v2"


def key_from_runtime() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is required for M3. Set it in the root .env file.")
    return key


class LLMError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status: int = 502,
        llm_ms: float | None = None,
        *,
        raw_response: str | None = None,
        response_model: str | None = None,
        provider: str | None = None,
        system_fingerprint: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.llm_ms = llm_ms
        self.raw_response = raw_response
        self.response_model = response_model
        self.provider = provider
        self.system_fingerprint = system_fingerprint
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


@dataclass(frozen=True)
class Interpretation:
    query: Query
    raw_response: str
    requested_model: str
    response_model: str
    provider: str | None
    system_fingerprint: str | None
    prompt_version: str
    llm_ms: float
    input_tokens: int | None
    output_tokens: int | None


def _values(records: list[TaskRecord], field: str) -> list[str]:
    values: set[str] = set()
    for record in records:
        value = getattr(record, field)
        if field == "labels":
            values.update(value)
        elif value is not None:
            values.add(str(value))
    return sorted(values)


def build_system_prompt(records: list[TaskRecord], reference_date) -> str:
    allowed = {field: _values(records, field) for field in ("status", "priority", "epic", "sprint", "assignee", "labels")}
    d0 = reference_date.isoformat()
    return f"""Ты компилятор запроса к фиксированной базе задач. Верни только объект Query по JSON Schema. Не отвечай на задачу сам и не добавляй условия, которых нет в запросе пользователя.

Дата эксперимента D0: {d0}. Все относительные даты считай от D0, а не от текущей даты.

Поля:
- status: {', '.join(allowed['status'])}
- priority: {', '.join(allowed['priority'])}
- epic: {', '.join(allowed['epic'])}
- sprint: {', '.join(allowed['sprint'])}
- assignee: {', '.join(allowed['assignee'])}; может отсутствовать
- labels: {', '.join(allowed['labels'])}; это множество меток
- created_at, deadline: дата YYYY-MM-DD; deadline может отсутствовать
- estimate_hours: целое число часов

Канонические правила:
1. Независимые условия соединяй AND. Альтернативы одного поля через «или» кодируй одним filter с operator=in и массивом значений. Не используй logic=OR, если достаточно in.
2. «За последние N дней» -> created_at >= D0-N дней И created_at <= D0, обе границы включены. «Исключить созданные более N дней назад» -> created_at >= D0-N дней.
3. «Просроченные» -> deadline < D0. «Без дедлайна» -> deadline is_null. «Исключить задачи без исполнителя» -> assignee not_null.
4. Для labels operator=eq означает наличие метки, neq — отсутствие метки.
5. COUNT не требует aggregation_field. Для SUM/AVG/MAX/MIN aggregation_field всегда estimate_hours.
6. Если требуется только агрегат без группировки, grouping.field=null. Если требуется «сгруппировать ... и найти группу с наибольшим/наименьшим ...», задай grouping, соответствующий aggregation, extremum.direction, extremum.metric и output.scope=extremum_group.
7. Если требуется экспорт, output.format=csv. Иначе table. Без экстремума output.scope=all_records, кроме явно требуемого aggregate_only.
8. Сортировку задавай только если она явно запрошена. Иначе sorting.field=null и sorting.direction=null.
9. Не придумывай значения. Используй только перечисленные категориальные значения и точные числовые/датовые условия из текста.
10. Перед ответом молча проверь: все явно запрошенные фильтры учтены; лишних фильтров нет; агрегат/группировка/экстремум согласованы; экспорт не потерян.

Примеры формата (это НЕ экспериментальные задания):

Запрос: «Покажи критические задачи эпика Профиль»
Ответ: {{"filters":[{{"field":"priority","operator":"eq","value":"Критический","logic":"AND"}},{{"field":"epic","operator":"eq","value":"Профиль","logic":"AND"}}],"grouping":{{"field":null,"aggregation":null,"aggregation_field":null}},"extremum":{{"direction":null,"metric":null}},"sorting":{{"field":null,"direction":null}},"output":{{"format":"table","scope":"all_records"}}}}

Запрос: «В Уведомлениях покажи открытые или готовые задачи за последние 10 дней и посчитай их»
Ответ: {{"filters":[{{"field":"epic","operator":"eq","value":"Уведомления","logic":"AND"}},{{"field":"status","operator":"in","value":["Открыта","Готово"],"logic":"AND"}},{{"field":"created_at","operator":"gte","value":"{(reference_date - timedelta(days=10)).isoformat()}","logic":"AND"}},{{"field":"created_at","operator":"lte","value":"{d0}","logic":"AND"}}],"grouping":{{"field":null,"aggregation":"COUNT","aggregation_field":null}},"extremum":{{"direction":null,"metric":null}},"sorting":{{"field":null,"direction":null}},"output":{{"format":"table","scope":"all_records"}}}}

Запрос: «Исключи отменённые, сгруппируй по приоритету, найди приоритет с максимальной средней оценкой задач с оценкой больше 6 часов и экспортируй»
Ответ: {{"filters":[{{"field":"status","operator":"neq","value":"Отменена","logic":"AND"}},{{"field":"estimate_hours","operator":"gt","value":6,"logic":"AND"}}],"grouping":{{"field":"priority","aggregation":"AVG","aggregation_field":"estimate_hours"}},"extremum":{{"direction":"max","metric":"avg"}},"sorting":{{"field":null,"direction":null}},"output":{{"format":"csv","scope":"extremum_group"}}}}

Запрос: «Задачи без дедлайна с критическим приоритетом и без метки баг: сгруппируй по эпикам, найди эпик с наибольшим количеством и экспортируй»
Ответ: {{"filters":[{{"field":"deadline","operator":"is_null","value":null,"logic":"AND"}},{{"field":"priority","operator":"eq","value":"Критический","logic":"AND"}},{{"field":"labels","operator":"neq","value":"баг","logic":"AND"}}],"grouping":{{"field":"epic","aggregation":"COUNT","aggregation_field":null}},"extremum":{{"direction":"max","metric":"count"}},"sorting":{{"field":null,"direction":null}},"output":{{"format":"csv","scope":"extremum_group"}}}}
"""


def _strict_schema(node: Any) -> Any:
    if isinstance(node, dict):
        result = {key: _strict_schema(value) for key, value in node.items() if key not in ("default", "title")}
        if result.get("type") == "object" and "properties" in result:
            result["additionalProperties"] = False
            result["required"] = list(result["properties"])
        return result
    if isinstance(node, list):
        return [_strict_schema(item) for item in node]
    return node


def query_response_schema() -> dict:
    return _strict_schema(Query.model_json_schema())


def prompt_digest(records: list[TaskRecord], reference_date) -> str:
    material = {
        "version": PROMPT_VERSION,
        "system": build_system_prompt(records, reference_date),
        "schema": query_response_schema(),
    }
    canonical = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _usage_token(usage: Any, key: str) -> int | None:
    value = usage.get(key) if isinstance(usage, dict) else None
    return value if isinstance(value, int) else None


def _response_model(payload: dict[str, Any] | None, fallback: str) -> str:
    value = payload.get("model") if isinstance(payload, dict) else None
    return str(value) if value else fallback


def _system_fingerprint(payload: dict[str, Any] | None) -> str | None:
    value = payload.get("system_fingerprint") if isinstance(payload, dict) else None
    return str(value) if value else None


def _response_provider(payload: dict[str, Any] | None) -> str | None:
    metadata = payload.get("openrouter_metadata") if isinstance(payload, dict) else None
    endpoints = metadata.get("endpoints") if isinstance(metadata, dict) else None
    available = endpoints.get("available") if isinstance(endpoints, dict) else None
    if not isinstance(available, list):
        return None
    for endpoint in available:
        if isinstance(endpoint, dict) and endpoint.get("selected") is True and endpoint.get("provider"):
            return str(endpoint["provider"])
    return None


class OpenAICompatibleInterpreter:
    def __init__(self, *, base_url: str, api_key: str, model: str, temperature: float, timeout_seconds: float,
                 records: list[TaskRecord], reference_date):
        self.endpoint = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.system_prompt = build_system_prompt(records, reference_date)
        self.schema = query_response_schema()
        self.prompt_sha256 = prompt_digest(records, reference_date)

    def interpret(self, text: str) -> Interpretation:
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "seed": 0,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": text},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "experiment_query", "strict": True, "schema": self.schema},
            },
            "provider": {"require_parameters": True},
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "X-OpenRouter-Metadata": "enabled",
            },
            method="POST",
        )
        started = time.perf_counter_ns()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read()
        except urllib.error.HTTPError as error:
            llm_ms = (time.perf_counter_ns() - started) / 1_000_000
            raise LLMError("llm_provider_error", f"LLM API вернул HTTP {error.code}. Повторите запрос.", 502, llm_ms) from None
        except (urllib.error.URLError, TimeoutError):
            llm_ms = (time.perf_counter_ns() - started) / 1_000_000
            raise LLMError("llm_unavailable", "LLM API недоступен или не ответил вовремя. Повторите запрос.", 503, llm_ms) from None
        llm_ms = (time.perf_counter_ns() - started) / 1_000_000
        raw_body = body.decode("utf-8", errors="replace")
        response_payload: dict[str, Any] | None = None
        content: str | None = None
        try:
            loaded = json.loads(raw_body)
            if not isinstance(loaded, dict):
                raise TypeError
            response_payload = loaded
            choice = response_payload["choices"][0]["message"]
            content = choice["content"]
            if not isinstance(content, str):
                raise TypeError
            parsed = json.loads(content)
            query = Query.model_validate(parsed)
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValidationError):
            usage = response_payload.get("usage") if isinstance(response_payload, dict) else None
            raise LLMError(
                "llm_invalid_response",
                "LLM вернул ответ, который не соответствует контракту Query. Повторите формулировку.",
                502,
                llm_ms,
                raw_response=content if isinstance(content, str) else raw_body,
                response_model=_response_model(response_payload, self.model),
                provider=_response_provider(response_payload),
                system_fingerprint=_system_fingerprint(response_payload),
                input_tokens=_usage_token(usage, "prompt_tokens"),
                output_tokens=_usage_token(usage, "completion_tokens"),
            ) from None
        usage = response_payload.get("usage")
        return Interpretation(
            query=query,
            raw_response=content,
            requested_model=self.model,
            response_model=_response_model(response_payload, self.model),
            provider=_response_provider(response_payload),
            system_fingerprint=_system_fingerprint(response_payload),
            prompt_version=PROMPT_VERSION,
            llm_ms=llm_ms,
            input_tokens=_usage_token(usage, "prompt_tokens"),
            output_tokens=_usage_token(usage, "completion_tokens"),
        )

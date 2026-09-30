"""M5 RouterAI tool-calling agent.

The observable policy mirrors benchmarks/m5_agent_benchmark.ipynb: seven atomic
operations, sequential execution of every returned tool call, and a capped LLM loop.
Hidden reasoning is neither requested nor persisted.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from .contracts import Extremum, Filter, Grouping, Output, Query, Sorting, TaskRecord
from .engine import execute

AGENT_PROMPT_VERSION = "m5-agent-v1"
FIELD_ENUM = ["id", "title", "status", "priority", "assignee", "epic", "sprint", "created_at", "deadline", "labels", "estimate_hours"]
GROUP_ENUM = ["status", "priority", "assignee", "epic", "sprint"]

TOOLS = [
    {"type": "function", "function": {"name": "search_tasks", "description": "Начать работу с фиксированной базой или сбросить текущую выборку ко всем 120 записям.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "filter_by_field", "description": "Отфильтровать ТЕКУЩУЮ выборку по одному полю. Несколько условий выполняй последовательно. Для operator=in/not_in value ОБЯЗАНО быть JSON-массивом. Для labels eq=метка есть, neq=метки нет.", "parameters": {"type": "object", "properties": {"field": {"type": "string", "enum": FIELD_ENUM}, "operator": {"type": "string", "enum": ["eq", "neq", "gt", "lt", "gte", "lte", "in", "not_in", "is_null", "not_null"]}, "value": {"description": "Для in/not_in — JSON-массив; для is_null/not_null — null; иначе одно скалярное значение.", "anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "array", "items": {"anyOf": [{"type": "string"}, {"type": "integer"}]}}, {"type": "null"}]}}, "required": ["field", "operator", "value"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "group_by", "description": "Сгруппировать текущую выборку. После этого вызови aggregate.", "parameters": {"type": "object", "properties": {"field": {"type": "string", "enum": GROUP_ENUM}}, "required": ["field"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "aggregate", "description": "Посчитать агрегат текущей выборки или каждой группы. COUNT: field=null; SUM/AVG/MAX/MIN: field=estimate_hours.", "parameters": {"type": "object", "properties": {"operation": {"type": "string", "enum": ["COUNT", "SUM", "AVG", "MAX", "MIN"]}, "field": {"anyOf": [{"type": "string", "enum": ["estimate_hours"]}, {"type": "null"}]}}, "required": ["operation", "field"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "find_extremum", "description": "После group_by + aggregate выбрать группу/группы с максимальным или минимальным агрегатом.", "parameters": {"type": "object", "properties": {"direction": {"type": "string", "enum": ["max", "min"]}}, "required": ["direction"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "sort", "description": "Сортировать текущую выборку только если пользователь явно просит сортировку.", "parameters": {"type": "object", "properties": {"field": {"type": "string", "enum": FIELD_ENUM}, "direction": {"type": "string", "enum": ["asc", "desc"]}}, "required": ["field", "direction"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "export_result", "description": "Экспортировать результат. После find_extremum используй scope=extremum_group. Вызывай только если пользователь явно просит экспорт.", "parameters": {"type": "object", "properties": {"format": {"type": "string", "enum": ["csv"]}, "scope": {"type": "string", "enum": ["all_records", "extremum_group"]}}, "required": ["format", "scope"], "additionalProperties": False}}},
]


def build_agent_prompt(reference_date) -> str:
    return f'''Ты агент для работы с фиксированной базой задач банковского приложения.
Твоя задача — выполнить пользовательскую цель ТОЛЬКО через доступные инструменты.

Дата эксперимента D0: {reference_date.isoformat()}. Все относительные даты вычисляй от D0, не от текущей даты.

Допустимые значения:
- status: Открыта, В работе, На ревью, Готово, Отменена
- priority: Низкий, Средний, Высокий, Критический
- epic (в интерфейсе «Направление работ»): Авторизация, Платежи, Уведомления, Профиль, Инфраструктура
- sprint (в интерфейсе «Рабочий цикл»): внутренние значения Спринт 1 ... Спринт 10; «Рабочий цикл N» означает «Спринт N»
- assignee: Участник 1 ... Участник 7; может отсутствовать
- labels: баг, фича, документация, рефакторинг
- created_at, deadline: YYYY-MM-DD; deadline может отсутствовать
- estimate_hours: целое число часов

Правила:
1. Не придумывай данные и не отвечай из общих знаний — используй инструменты.
2. Независимые фильтры применяй последовательными вызовами filter_by_field.
3. Альтернативы одного поля через «ИЛИ» передавай одним filter_by_field с operator=in. При operator=in/not_in value ОБЯЗАТЕЛЬНО передавай как JSON-массив.
4. «За последние N дней»: created_at >= D0-N дней и created_at <= D0, обе границы включены.
5. «Исключить созданные более N дней назад»: created_at >= D0-N дней.
6. «Просроченные»: deadline < D0 И status != "Готово".
7. «Без дедлайна»: deadline is_null.
8. «Исключить задачи без исполнителя»: assignee not_null.
9. Для labels: eq = метка присутствует, neq = метка отсутствует.
10. Для группового экстремума порядок обязателен: group_by → aggregate → find_extremum.
11. Если пользователь просит экспорт, после вычисления результата обязательно вызови export_result.
12. Не вызывай sort, если сортировка явно не нужна.
13. Когда задача полностью выполнена, дай очень короткое подтверждение.'''


def agent_prompt_digest(reference_date) -> str:
    material = {"version": AGENT_PROMPT_VERSION, "system": build_agent_prompt(reference_date), "tools": TOOLS}
    return hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class AgentError(Exception):
    def __init__(self, code: str, message: str, status: int = 502, **diagnostics):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
        self.diagnostics = diagnostics


@dataclass
class AgentExecution:
    query: Query
    trajectory: list[dict[str, Any]]
    requested_model: str
    response_model: str | None
    provider: str | None
    prompt_version: str
    prompt_sha256: str
    llm_ms: float
    tool_ms: float
    total_ms: float
    llm_calls: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    final_text: str | None
    termination: str


@dataclass
class AgentState:
    records: list[TaskRecord]
    filters: list[Filter] = field(default_factory=list)
    grouping_field: str | None = None
    aggregation: str | None = None
    aggregation_field: str | None = None
    extremum_direction: str | None = None
    sorting_field: str | None = None
    sorting_direction: str | None = None
    output_format: str = "table"
    output_scope: str = "all_records"

    def reset(self):
        self.filters.clear(); self.grouping_field = None; self.aggregation = None; self.aggregation_field = None
        self.extremum_direction = None; self.sorting_field = None; self.sorting_direction = None
        self.output_format = "table"; self.output_scope = "all_records"

    def query(self) -> Query:
        metric = self.aggregation.lower() if self.extremum_direction and self.aggregation else None
        return Query(
            filters=list(self.filters),
            grouping=Grouping(field=self.grouping_field, aggregation=self.aggregation, aggregation_field=self.aggregation_field),
            extremum=Extremum(direction=self.extremum_direction, metric=metric),
            sorting=Sorting(field=self.sorting_field, direction=self.sorting_direction if self.sorting_field else None),
            output=Output(format=self.output_format, scope=self.output_scope),
        )

    def snapshot(self) -> dict[str, Any]:
        q = self.query()
        result = execute(self.records, q)
        return {"query": q.model_dump(mode="json"), "record_count": len(result.record_ids), "aggregate": result.value,
                "selected_groups": result.selected_groups}

    def execute_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "search_tasks":
            self.reset()
            return {"ok": True, "record_count": len(self.records), "fields": FIELD_ENUM}
        if name == "filter_by_field":
            operator, value = args.get("operator"), args.get("value")
            if operator in ("in", "not_in") and not isinstance(value, list):
                return {"ok": False, "error": "INVALID_ARGUMENT", "message": f"For operator '{operator}', value MUST be a JSON array.", "state_unchanged": True, "remaining_count": len(execute(self.records, self.query()).record_ids)}
            if operator not in ("in", "not_in", "is_null", "not_null") and isinstance(value, list):
                return {"ok": False, "error": "INVALID_ARGUMENT", "message": f"For operator '{operator}', value MUST be a scalar, not an array.", "state_unchanged": True, "remaining_count": len(execute(self.records, self.query()).record_ids)}
            try:
                rule = Filter(field=args["field"], operator=operator, value=value)
                candidate = Query(filters=[*self.filters, rule])
                execute(self.records, candidate)
            except (KeyError, ValidationError, Exception) as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                return {"ok": False, "error": type(exc).__name__, "message": str(exc), "state_unchanged": True}
            self.filters.append(rule)
            return {"ok": True, "remaining_count": len(execute(self.records, Query(filters=self.filters)).record_ids)}
        if name == "group_by":
            if args.get("field") not in GROUP_ENUM: raise ValueError("invalid group field")
            self.grouping_field = args["field"]; self.aggregation = None; self.aggregation_field = None; self.extremum_direction = None
            self.output_scope = "all_records"
            result = execute(self.records, self.query())
            return {"ok": True, "group_field": self.grouping_field, "group_count": len(result.groups), "groups": [g.key for g in result.groups]}
        if name == "aggregate":
            op, fld = args.get("operation"), args.get("field")
            if op not in ("COUNT", "SUM", "AVG", "MAX", "MIN"): raise ValueError("invalid aggregate")
            if (op == "COUNT" and fld is not None) or (op != "COUNT" and fld != "estimate_hours"):
                raise ValueError("COUNT requires field=null; numeric aggregates require estimate_hours")
            self.aggregation, self.aggregation_field = op, fld
            result = execute(self.records, self.query())
            return {"ok": True, "operation": op, "value": result.value,
                    "group_values": {g.key: g.value for g in result.groups} if result.groups else None}
        if name == "find_extremum":
            if not self.grouping_field or not self.aggregation: raise ValueError("find_extremum requires group_by + aggregate")
            if args.get("direction") not in ("max", "min"): raise ValueError("invalid direction")
            self.extremum_direction = args["direction"]
            result = execute(self.records, self.query())
            return {"ok": True, "selected_groups": result.selected_groups, "value": result.value,
                    "record_count": len(result.record_ids)}
        if name == "sort":
            if args.get("field") not in FIELD_ENUM or args.get("direction") not in ("asc", "desc"):
                raise ValueError("invalid sort")
            self.sorting_field, self.sorting_direction = args["field"], args["direction"]
            return {"ok": True, "field": self.sorting_field, "direction": self.sorting_direction,
                    "record_count": len(execute(self.records, self.query()).record_ids)}
        if name == "export_result":
            if args.get("format") != "csv" or args.get("scope") not in ("all_records", "extremum_group"):
                raise ValueError("invalid export")
            if args["scope"] == "extremum_group" and not self.extremum_direction:
                raise ValueError("extremum_group requires find_extremum")
            self.output_format, self.output_scope = "csv", args["scope"]
            result = execute(self.records, self.query())
            return {"ok": True, "format": "csv", "scope": self.output_scope, "record_count": len(result.record_ids),
                    "record_ids": result.record_ids, "aggregate": result.value, "selected_groups": result.selected_groups}
        raise KeyError(name)


def _provider(payload: dict[str, Any]) -> str | None:
    value = payload.get("provider") or payload.get("provider_name")
    return str(value) if value else None


class RouterAIAgent:
    def __init__(self, *, base_url: str, api_key: str, model: str, temperature: float, timeout_seconds: float,
                 max_steps: int, records: list[TaskRecord], reference_date):
        self.endpoint = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key; self.model = model; self.temperature = temperature; self.timeout_seconds = timeout_seconds
        self.max_steps = max_steps; self.records = records; self.system_prompt = build_agent_prompt(reference_date)
        self.prompt_sha256 = agent_prompt_digest(reference_date)

    def _call(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        body = {"model": self.model, "temperature": self.temperature, "messages": messages, "tools": TOOLS,
                "tool_choice": "auto"}
        request = urllib.request.Request(self.endpoint, data=json.dumps(body, ensure_ascii=False).encode(),
                                         headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, method="POST")
        started = time.perf_counter_ns()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            ms = (time.perf_counter_ns() - started) / 1_000_000
            raise AgentError("agent_provider_error", f"RouterAI вернул HTTP {error.code}. Повторите запрос.", 502, llm_ms=ms) from None
        except (urllib.error.URLError, TimeoutError):
            ms = (time.perf_counter_ns() - started) / 1_000_000
            raise AgentError("agent_unavailable", "RouterAI недоступен или не ответил вовремя. Повторите запрос.", 503, llm_ms=ms) from None
        ms = (time.perf_counter_ns() - started) / 1_000_000
        try:
            payload = json.loads(raw); message = payload["choices"][0]["message"]
            if not isinstance(message, dict): raise TypeError
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            raise AgentError("agent_invalid_response", "RouterAI вернул некорректный ответ agent loop.", 502, llm_ms=ms) from None
        usage = payload.get("usage") or {}
        return {"message": message, "elapsed_ms": ms, "prompt_tokens": usage.get("prompt_tokens") or 0,
                "completion_tokens": usage.get("completion_tokens") or 0, "provider": _provider(payload),
                "response_model": payload.get("model")}

    def run(self, text: str) -> AgentExecution:
        state = AgentState(self.records)
        messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}, {"role": "user", "content": text}]
        trajectory: list[dict[str, Any]] = []
        llm_ms = tool_ms = 0.0; llm_calls = tool_calls = input_tokens = output_tokens = 0
        response_model = provider = final_text = None; termination = "max_steps"
        total_started = time.perf_counter_ns()
        for step in range(1, self.max_steps + 1):
            response = self._call(messages); llm_calls += 1; llm_ms += response["elapsed_ms"]
            input_tokens += response["prompt_tokens"]; output_tokens += response["completion_tokens"]
            response_model = response["response_model"] or response_model; provider = response["provider"] or provider
            message = response["message"]; calls = message.get("tool_calls") or []; final_text = message.get("content")
            trajectory.append({"step": step, "type": "llm", "latency_ms": round(response["elapsed_ms"], 3),
                               "content": final_text, "tool_calls_requested": len(calls), "provider": response["provider"],
                               "response_model": response["response_model"], "prompt_tokens": response["prompt_tokens"],
                               "completion_tokens": response["completion_tokens"]})
            assistant = {"role": "assistant", "content": message.get("content")}
            if calls: assistant["tool_calls"] = calls
            messages.append(assistant)
            if not calls:
                termination = "model_finished"; break
            for tc in calls:
                name = tc.get("function", {}).get("name", "")
                try:
                    args = json.loads(tc.get("function", {}).get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {"__invalid_json__": tc.get("function", {}).get("arguments")}
                started = time.perf_counter_ns()
                try:
                    if "__invalid_json__" in args: raise ValueError("Invalid JSON arguments")
                    result = state.execute_tool(name, args); ok = bool(result.get("ok", True))
                except Exception as exc:
                    result = {"ok": False, "error": type(exc).__name__, "message": str(exc)}; ok = False
                elapsed = (time.perf_counter_ns() - started) / 1_000_000; tool_ms += elapsed; tool_calls += 1
                trajectory.append({"step": step, "type": "tool", "tool": name, "args": args, "result": result,
                                   "ok": ok, "latency_ms": round(elapsed, 4), "state": state.snapshot()})
                messages.append({"role": "tool", "tool_call_id": tc.get("id"), "content": json.dumps(result, ensure_ascii=False, default=str)})
        total_ms = (time.perf_counter_ns() - total_started) / 1_000_000
        return AgentExecution(query=state.query(), trajectory=trajectory, requested_model=self.model, response_model=response_model,
                              provider=provider, prompt_version=AGENT_PROMPT_VERSION, prompt_sha256=self.prompt_sha256,
                              llm_ms=llm_ms, tool_ms=tool_ms, total_ms=total_ms, llm_calls=llm_calls, tool_calls=tool_calls,
                              input_tokens=input_tokens, output_tokens=output_tokens, final_text=final_text, termination=termination)

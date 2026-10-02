from datetime import date
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Mode(StrEnum):
    M1 = "M1"
    M2 = "M2"
    M3 = "M3"
    M4 = "M4"
    M5 = "M5"


BASE_AVAILABLE_MODES = frozenset({Mode.M1, Mode.M2})


class TrialStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    CORRECT = "correct"
    INCOMPLETE = "incomplete"


class TaskRecord(Contract):
    id: str
    title: str
    status: Literal["Открыта", "В работе", "На ревью", "Готово", "Отменена"]
    priority: Literal["Низкий", "Средний", "Высокий", "Критический"]
    assignee: str | None
    epic: str
    sprint: str
    created_at: date
    deadline: date | None
    labels: list[str]
    estimate_hours: int = Field(ge=1, le=40)


FieldName = Literal[
    "id", "title", "status", "priority", "assignee", "epic", "sprint",
    "created_at", "deadline", "labels", "estimate_hours",
]
GroupField = Literal["status", "priority", "assignee", "epic", "sprint"]
Aggregation = Literal["COUNT", "SUM", "AVG", "MAX", "MIN"]


class Filter(Contract):
    field: FieldName
    operator: Literal["eq", "neq", "gt", "lt", "gte", "lte", "in", "not_in", "is_null", "not_null"]
    value: str | StrictInt | list[str | StrictInt] | None = None
    logic: Literal["AND", "OR"] = "AND"


class Grouping(Contract):
    field: GroupField | None = None
    aggregation: Aggregation | None = None
    aggregation_field: Literal["estimate_hours"] | None = None

    @model_validator(mode="after")
    def compatible_aggregation(self):
        if self.aggregation not in (None, "COUNT") and self.aggregation_field is None:
            raise ValueError("Для числового агрегата требуется aggregation_field.")
        if self.aggregation in (None, "COUNT") and self.aggregation_field is not None:
            raise ValueError("Для COUNT или отсутствующего агрегата поле не задаётся.")
        return self


class Extremum(Contract):
    direction: Literal["max", "min"] | None = None
    metric: Literal["count", "sum", "avg", "max", "min"] | None = None


class Sorting(Contract):
    field: FieldName | None = None
    direction: Literal["asc", "desc"] | None = "asc"


class Output(Contract):
    format: Literal["table", "csv", "chart", "summary"] = "table"
    scope: Literal["all_records", "extremum_group", "aggregate_only"] = "all_records"


class Query(Contract):
    filters: list[Filter] = Field(default_factory=list, max_length=40)
    grouping: Grouping = Field(default_factory=Grouping)
    extremum: Extremum = Field(default_factory=Extremum)
    sorting: Sorting = Field(default_factory=Sorting)
    output: Output = Field(default_factory=Output)

    @model_validator(mode="after")
    def compatible_operations(self):
        if self.extremum.direction:
            if not self.grouping.field or not self.grouping.aggregation:
                raise ValueError("Экстремум требует группировку и агрегат.")
            if self.extremum.metric != self.grouping.aggregation.lower():
                raise ValueError("Метрика экстремума должна совпадать с агрегатом.")
        elif self.extremum.metric:
            raise ValueError("Для метрики экстремума требуется direction.")
        if self.output.scope == "extremum_group" and not self.extremum.direction:
            raise ValueError("Область extremum_group требует выбор экстремума.")
        if self.output.scope == "aggregate_only" and not self.grouping.aggregation:
            raise ValueError("Область aggregate_only требует агрегат.")
        if self.sorting.field == "labels":
            raise ValueError("Сортировка по множеству меток не поддерживается.")
        if self.sorting.field and self.sorting.direction is None:
            raise ValueError("Для сортировки требуется direction.")
        return self


class GroupResult(Contract):
    key: str | None
    value: str | None
    record_ids: list[str]


class QueryResult(Contract):
    record_ids: list[str]
    records: list[TaskRecord]
    value: str | None
    groups: list[GroupResult]
    selected_groups: list[str | None]
    tie: bool


class PreviewView(Contract):
    request_id: UUID
    query: Query
    result: QueryResult


class M4TranscriptionView(Contract):
    request_id: UUID
    text: str
    model: str
    detected_language: str | None
    language_probability: float | None
    asr_ms: float


class SessionCreate(Contract):
    participant_code: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    kind: Literal["experiment", "practice"] = "experiment"


class AttemptInput(Contract):
    request_id: UUID
    query: Query


class M3AttemptInput(Contract):
    request_id: UUID
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("text")
    @classmethod
    def non_blank_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Текст запроса не может быть пустым.")
        return value


class M5AttemptInput(Contract):
    request_id: UUID
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("text")
    @classmethod
    def non_blank_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Текст цели не может быть пустым.")
        return value


class Event(Contract):
    event_id: UUID
    sequence: int = Field(ge=0)
    offset_ms: int = Field(ge=0)
    kind: Literal["input", "focus_lost", "focus_gained", "request_started", "request_finished", "speech_started", "speech_finished", "navigation"]
    target: str = Field(default="", max_length=120)
    action: Literal["click", "keydown", "pointermove", "scroll", "change"] | None = None
    x: float | None = Field(default=None, allow_inf_nan=False)
    y: float | None = Field(default=None, allow_inf_nan=False)
    key: str | None = Field(default=None, max_length=32)
    request_id: UUID | None = None

    @model_validator(mode="after")
    def request_pairing(self):
        if self.kind.startswith("request_") and self.request_id is None:
            raise ValueError("Событие запроса требует request_id.")
        return self


class EventBatch(Contract):
    events: list[Event] = Field(min_length=1, max_length=500)


class ActualMetrics(Contract):
    elapsed_ms: int | None
    Tcorrect_ms: int | None
    Tfirst_ms: int | None
    Tuser_active_ms: int
    A1: int | None
    attempts: int
    Nretry: int


class AnalysisMetrics(Contract):
    Tcorrect_ms: int | None
    A1: int | None
    Nretry: int


class Metrics(Contract):
    actual: ActualMetrics
    analysis: AnalysisMetrics
    incomplete: bool
    end_reason: str | None


class AttemptSummary(Contract):
    id: str
    ordinal: int
    correct: bool
    started_ms: int
    finished_ms: int


class TrialView(Contract):
    wording_version: str
    trial_limit_seconds: int
    attempt_limit: int
    id: str
    session_id: str
    position: int
    block_index: int
    mode: Mode
    task_id: str
    status: TrialStatus
    started_ms: int | None
    ended_ms: int | None
    end_reason: str | None
    prompt: str | None
    tci: int
    level: int
    mode_available: bool
    deadline_ms: int | None
    attempts: list[AttemptSummary]
    next_event_sequence: int
    last_event_offset_ms: int
    elapsed_since_start_ms: int
    metrics: Metrics


class SessionView(Contract):
    id: str
    participant_code: str
    kind: Literal["experiment", "practice"]
    sequence_no: int
    created_ms: int
    completion_code: str | None = None
    complete: bool
    manifest: dict
    trials: list[TrialView]


class AttemptView(AttemptSummary):
    trial_id: str
    trial_status: TrialStatus
    end_reason: str | None
    result: QueryResult
    Texec_ms: float
    export_url: str | None

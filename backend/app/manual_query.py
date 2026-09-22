"""Explicit manual tag grammar for M2, without intent inference."""
import re
from datetime import timedelta
from typing import Literal

from pydantic import Field, ValidationError

from .contracts import Contract, Filter, Query
from .errors import DomainError

FIELDS = {"статус": "status", "приоритет": "priority", "исполнитель": "assignee", "эпик": "epic",
          "спринт": "sprint", "метка": "labels", "дата создания": "created_at", "дедлайн": "deadline", "оценка": "estimate_hours"}
GROUPS = {"эпик": "epic", "спринт": "sprint", "исполнитель": "assignee", "статус": "status", "приоритет": "priority"}
AGGREGATIONS = {"количество": "COUNT", "сумма часов": "SUM", "средняя оценка": "AVG", "максимальная оценка": "MAX", "минимальная оценка": "MIN"}
OPERATORS = {":": "eq", "=": "eq", "!=": "neq", ">": "gt", "<": "lt", ">=": "gte", "<=": "lte"}


class ManualTags(Contract):
    tags: list[str] = Field(min_length=1, max_length=40)


class TagBuilder(Contract):
    label: str
    kind: Literal["filter", "action"]
    operators: list[str]
    values: list[str]
    placeholder: str
    alternatives: bool = False
    exclusive_values: list[str] = Field(default_factory=list)


def compile_tags(tags: list[str], records, reference_date) -> Query:
    query = Query()
    operations = set()
    for index, raw in enumerate(tags):
        if len(raw) > 128:
            raise DomainError("invalid_tag", f"Условие {index + 1}: не более 128 символов.")
        match = re.fullmatch(r"\s*([^:!=<>]+?)\s*(:|!=|>=|<=|=|>|<)\s*(.+?)\s*", raw)
        if not match:
            raise DomainError("invalid_tag", f"Условие {index + 1}: используйте формат «Поле: значение».")
        name, operator, value = match.groups()
        name = name.strip().casefold()
        value = value.strip()
        folded = value.casefold()
        if name not in FIELDS:
            if operator != ":" or name in operations:
                raise DomainError("invalid_tag", f"Операция {index + 1}: используйте двоеточие и задайте её один раз.")
            operations.add(name)
            if name == "период":
                period = re.fullmatch(r"([1-9][0-9]{0,3})\s+(?:день|дня|дней)", folded)
                if not period:
                    raise DomainError("invalid_tag", "Период: введите число дней, например «Период: 14 дней».")
                query.filters.extend([
                    Filter(field="created_at", operator="gte", value=(reference_date - timedelta(days=int(period[1]))).isoformat()),
                    Filter(field="created_at", operator="lte", value=reference_date.isoformat()),
                ])
            elif name == "итог" and folded in AGGREGATIONS:
                query.grouping.aggregation = AGGREGATIONS[folded]
                query.grouping.aggregation_field = None if folded == "количество" else "estimate_hours"
            elif name == "группировка" and folded in GROUPS:
                query.grouping.field = GROUPS[folded]
            elif name == "экстремум" and folded in ("максимум", "минимум"):
                query.extremum.direction = "max" if folded == "максимум" else "min"
            elif name == "экспорт" and folded == "csv":
                query.output.format = "csv"
            elif name == "сортировка":
                sort = re.fullmatch(r"(.+?)\s+(возрастание|убывание)", folded)
                if not sort or sort[1] not in FIELDS:
                    raise DomainError("invalid_tag", "Сортировка: укажите поле и «возрастание» или «убывание».")
                query.sorting.field = FIELDS[sort[1]]
                query.sorting.direction = "asc" if sort[2] == "возрастание" else "desc"
            else:
                raise DomainError("invalid_tag", f"Операция {index + 1} не распознана. Выберите вариант из подсказок.")
            continue
        field = FIELDS[name]
        if field in ("deadline", "assignee") and operator == ":" and folded in ("отсутствует", "указан"):
            query.filters.append(Filter(field=field, operator="is_null" if folded == "отсутствует" else "not_null"))
            continue
        if field == "deadline" and operator == ":" and folded == "просрочен":
            query.filters.append(Filter(field=field, operator="lt", value=reference_date.isoformat()))
            continue
        op = OPERATORS[operator]
        values = [part.strip() for part in value.split("/")]
        if any(not part for part in values) or (len(values) > 1 and op not in ("eq", "neq")):
            raise DomainError("invalid_tag", f"Условие {index + 1}: неверный список альтернатив.")
        if field == "estimate_hours":
            if any(not part.isdigit() for part in values):
                raise DomainError("invalid_tag", "Оценка должна быть целым числом часов.")
            values = [int(part) for part in values]
        elif field not in ("created_at", "deadline"):
            allowed = {str(v).casefold(): str(v) for record in records
                       for v in (getattr(record, field) if field == "labels" else [getattr(record, field)]) if v is not None}
            if any(part.casefold() not in allowed for part in values):
                raise DomainError("invalid_tag", f"Условие {index + 1}: значение отсутствует в допустимых вариантах.")
            values = [allowed[part.casefold()] for part in values]
        if len(values) > 1:
            op = "in" if op == "eq" else "not_in"
        query.filters.append(Filter(field=field, operator=op, value=values if len(values) > 1 else values[0]))
    if query.extremum.direction:
        query.extremum.metric = query.grouping.aggregation.lower() if query.grouping.aggregation else None
        query.output.scope = "extremum_group"
    try:
        return Query.model_validate(query.model_dump())
    except ValidationError:
        raise DomainError("invalid_operations", "Проверьте операции: экстремум требует группировку и итог.") from None


def suggestions(records):
    result = []
    for label, field in FIELDS.items():
        if field in ("created_at", "deadline", "estimate_hours"):
            continue
        values = sorted({str(value) for record in records for value in
                         (record.labels if field == "labels" else [getattr(record, field)]) if value is not None})
        result.extend(f"{label.capitalize()}: {value}" for value in values)
    result.extend(f"Итог: {label}" for label in AGGREGATIONS)
    result.extend(f"Группировка: {label}" for label in GROUPS)
    result.extend(["Период: 14 дней", "Дедлайн: просрочен", "Дедлайн: отсутствует", "Исполнитель: указан",
                   "Исполнитель: отсутствует", "Оценка > 8", "Сортировка: дата создания убывание",
                   "Экстремум: максимум", "Экстремум: минимум", "Экспорт: CSV"])
    return result


def builders(records) -> list[TagBuilder]:
    hints = suggestions(records)
    result = []
    for label, field in FIELDS.items():
        title = label.capitalize()
        prefix = title + ": "
        numeric = field == "estimate_hours"
        dated = field in ("created_at", "deadline")
        result.append(TagBuilder(
            label=title, kind="filter",
            operators=[":", "!=", ">", ">=", "<", "<="] if numeric or dated else [":", "!="],
            values=[hint[len(prefix):] for hint in hints if hint.startswith(prefix)],
            placeholder="Часы, например 8" if numeric else "Дата ГГГГ-ММ-ДД" if dated else "Выберите или введите значение",
            alternatives=not (numeric or dated),
            exclusive_values=["отсутствует", "указан", "просрочен"] if field == "deadline" else ["отсутствует", "указан"] if field == "assignee" else [],
        ))
    result.append(TagBuilder(label="Период", kind="filter", operators=[":"], values=["7 дней", "14 дней", "21 день", "30 дней", "60 дней"], placeholder="Например, 14 дней"))
    for label, values in (
        ("Итог", list(AGGREGATIONS)), ("Группировка", list(GROUPS)),
        ("Экстремум", ["максимум", "минимум"]), ("Экспорт", ["CSV"]),
        ("Сортировка", [f"{field} {direction}" for field in FIELDS if FIELDS[field] != "labels" for direction in ("возрастание", "убывание")]),
    ):
        result.append(TagBuilder(label=label, kind="action", operators=[":"], values=values, placeholder="Выберите или введите действие"))
    return result

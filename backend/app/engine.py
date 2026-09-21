import csv
import io
from collections import defaultdict
from datetime import date
from decimal import Decimal

from .contracts import Filter, GroupResult, Query, QueryResult, TaskRecord
from .errors import DomainError


def _operand(rule: Filter, value):
    if rule.field in ("created_at", "deadline"):
        try:
            return date.fromisoformat(value) if isinstance(value, str) else _bad_value()
        except ValueError:
            _bad_value()
    if rule.field == "estimate_hours":
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        _bad_value()
    if not isinstance(value, str):
        _bad_value()
    return value


def _bad_value():
    raise DomainError("invalid_filter_value", "Тип значения фильтра не соответствует полю.")


def _prepare(rule: Filter):
    op = rule.operator
    if op in ("is_null", "not_null"):
        if rule.value is not None or rule.field not in ("assignee", "deadline"):
            raise DomainError("invalid_null_filter", "Проверка null доступна для исполнителя и дедлайна без value.")
        return None
    if op in ("in", "not_in"):
        if not isinstance(rule.value, list) or not rule.value:
            raise DomainError("invalid_filter_value", "in/not_in требуют непустой список.")
        return [_operand(rule, item) for item in rule.value]
    if op in ("gt", "lt", "gte", "lte") and rule.field not in ("created_at", "deadline", "estimate_hours"):
        raise DomainError("invalid_operator", "Сравнение порядка доступно только для дат и оценки.")
    return _operand(rule, rule.value)


def _matches(record: TaskRecord, rule: Filter, expected) -> bool:
    actual = getattr(record, rule.field)
    op = rule.operator
    if op == "is_null":
        return actual is None
    if op == "not_null":
        return actual is not None
    if actual is None:
        return False
    if rule.field == "labels":
        included = expected in actual if op in ("eq", "neq") else bool(set(actual).intersection(expected))
        return not included if op in ("neq", "not_in") else included
    if op == "eq":
        return actual == expected
    if op == "neq":
        return actual != expected
    if op == "in":
        return actual in expected
    if op == "not_in":
        return actual not in expected
    if op == "gt":
        return actual > expected
    if op == "gte":
        return actual >= expected
    if op == "lt":
        return actual < expected
    return actual <= expected


def aggregate(records: list[TaskRecord], operation: str | None, field: str | None) -> Decimal | None:
    if operation is None:
        return None
    if operation == "COUNT":
        return Decimal(len(records))
    values = [Decimal(getattr(record, field)) for record in records]
    if operation == "SUM":
        return sum(values, Decimal(0))
    if not values:
        return None
    if operation == "AVG":
        return sum(values) / len(values)
    if operation == "MAX":
        return max(values)
    return min(values)


def execute(records: list[TaskRecord], query: Query) -> QueryResult:
    # AND of groups; contiguous OR is allowed only for equality alternatives
    # of the same field. Unsupported expressions fail, never change meaning.
    clauses: list[list[tuple[Filter, object]]] = []
    for rule in query.filters:
        expected = _prepare(rule)
        if rule.logic == "OR":
            if not clauses or rule.operator != "eq" or any(
                previous.field != rule.field or previous.operator != "eq"
                for previous, _ in clauses[-1]
            ):
                raise DomainError("ambiguous_logic", "OR объединяет только соседние eq одного поля; используйте in.")
            clauses[-1].append((rule, expected))
        else:
            clauses.append([(rule, expected)])
    filtered = [r for r in records if all(any(_matches(r, f, v) for f, v in clause) for clause in clauses)]
    grouped: dict[str | None, list[TaskRecord]] = defaultdict(list)
    groups: list[GroupResult] = []
    selected: list[str | None] = []
    value = None
    if query.grouping.field:
        for record in filtered:
            grouped[getattr(record, query.grouping.field)].append(record)
        for key in sorted(grouped, key=lambda k: (k is None, k or "")):
            group_value = aggregate(grouped[key], query.grouping.aggregation, query.grouping.aggregation_field)
            groups.append(GroupResult(key=key, value=str(group_value) if group_value is not None else None,
                                      record_ids=sorted(r.id for r in grouped[key])))
        if query.extremum.direction and groups:
            choose = max if query.extremum.direction == "max" else min
            value = choose(Decimal(group.value) for group in groups)
            selected = [group.key for group in groups if Decimal(group.value) == value]
            if query.output.scope in ("extremum_group", "aggregate_only"):
                filtered = [r for r in filtered if getattr(r, query.grouping.field) in selected]
    else:
        value = aggregate(filtered, query.grouping.aggregation, query.grouping.aggregation_field)
    filtered.sort(key=lambda r: r.id)
    if query.sorting.field:
        field = query.sorting.field
        not_null = [r for r in filtered if getattr(r, field) is not None]
        null = [r for r in filtered if getattr(r, field) is None]
        not_null.sort(key=lambda r: getattr(r, field), reverse=query.sorting.direction == "desc")
        filtered = not_null + null
    return QueryResult(record_ids=[r.id for r in filtered], records=filtered,
                       value=str(value) if value is not None else None, groups=groups,
                       selected_groups=selected, tie=len(selected) > 1)


def verify(actual: QueryResult, expected: QueryResult, *, check_aggregate: bool,
           average: bool, tolerance: Decimal, requires_export: bool, exported: bool) -> bool:
    if set(actual.record_ids) != set(expected.record_ids):
        return False
    if requires_export and not exported:
        return False
    if not check_aggregate:
        return True
    if actual.value is None or expected.value is None:
        return actual.value == expected.value
    delta = abs(Decimal(actual.value) - Decimal(expected.value))
    return delta <= tolerance if average else delta == 0


def csv_result(result: QueryResult, query: Query) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    if query.output.scope == "aggregate_only":
        writer.writerow(["group", "value"])
        if result.groups:
            for group in result.groups:
                if not query.extremum.direction or group.key in result.selected_groups:
                    writer.writerow([_csv_cell(group.key), group.value])
        else:
            writer.writerow(["", result.value])
    else:
        fields = list(TaskRecord.model_fields)
        writer.writerow(fields)
        for record in result.records:
            values = record.model_dump(mode="json")
            writer.writerow([_csv_cell(values[field]) for field in fields])
    return "\ufeff" + stream.getvalue()


def _csv_cell(value):
    if isinstance(value, list):
        value = ";".join(value)
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value

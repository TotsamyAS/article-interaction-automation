import csv
import io
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contracts import Query, TaskRecord
from app.engine import csv_result, execute, verify
from app.errors import DomainError


def record(identifier, **overrides):
    values = dict(id=identifier, title="Задача", status="В работе", priority="Высокий",
                  assignee="Участник 1", epic="Платежи", sprint="Спринт 1", created_at="2026-08-18",
                  deadline=None, labels=["баг"], estimate_hours=10)
    values.update(overrides)
    return TaskRecord(**values)


def test_date_boundaries_null_and_label_membership():
    records = [record("1"), record("2", created_at="2026-09-01"), record("3", created_at="2026-08-17"),
               record("4", created_at="2026-09-02"), record("5", labels=["фича"])]
    query = Query.model_validate({"filters": [
        {"field": "created_at", "operator": "gte", "value": "2026-08-18"},
        {"field": "created_at", "operator": "lte", "value": "2026-09-01"},
        {"field": "labels", "operator": "in", "value": ["баг", "рефакторинг"]},
        {"field": "deadline", "operator": "is_null"},
    ]})
    assert execute(records, query).record_ids == ["1", "2"]


def test_or_alternatives_stay_inside_other_filters():
    records = [record("1"), record("2", status="На ревью"), record("3", status="На ревью", epic="Профиль")]
    query = Query.model_validate({"filters": [
        {"field": "epic", "operator": "eq", "value": "Платежи"},
        {"field": "status", "operator": "eq", "value": "В работе"},
        {"field": "status", "operator": "eq", "value": "На ревью", "logic": "OR"},
    ]})
    assert execute(records, query).record_ids == ["1", "2"]
    query.filters[-1].field = "priority"
    with pytest.raises(DomainError, match="OR"):
        execute(records, query)


def test_extremum_returns_every_tied_group_and_only_filtered_records():
    records = [record("1", epic="A"), record("2", epic="B"), record("3", epic="A", status="Отменена")]
    query = Query.model_validate({
        "filters": [{"field": "status", "operator": "neq", "value": "Отменена"}],
        "grouping": {"field": "epic", "aggregation": "COUNT"},
        "extremum": {"direction": "max", "metric": "count"},
        "output": {"format": "csv", "scope": "extremum_group"},
    })
    result = execute(records, query)
    assert result.record_ids == ["1", "2"]
    assert result.selected_groups == ["A", "B"]
    assert result.tie and result.value == "1"
    rows = list(csv.DictReader(io.StringIO(csv_result(result, query).lstrip("\ufeff"))))
    assert [row["id"] for row in rows] == ["1", "2"]


def test_verification_requires_exact_ids_and_export_and_average_tolerance():
    query = Query.model_validate({"grouping": {"aggregation": "AVG", "aggregation_field": "estimate_hours"}})
    expected = execute([record("1", estimate_hours=10), record("2", estimate_hours=11)], query)
    options = dict(check_aggregate=True, average=True, tolerance=Decimal("0.01"), requires_export=True, exported=True)
    assert verify(expected.model_copy(update={"value": "10.51"}), expected, **options)
    assert not verify(expected.model_copy(update={"value": "10.52"}), expected, **options)
    assert not verify(expected.model_copy(update={"record_ids": ["1"]}), expected, **options)
    assert not verify(expected, expected, **(options | {"exported": False}))


@pytest.mark.parametrize("value", ["not-a-date", 123, None])
def test_invalid_filter_is_rejected_even_for_empty_dataset(value):
    query = Query.model_validate({"filters": [{"field": "deadline", "operator": "lt", "value": value}]})
    with pytest.raises(DomainError):
        execute([], query)


def test_incompatible_aggregation_rejected_at_contract_boundary():
    with pytest.raises(ValidationError):
        Query.model_validate({"grouping": {"aggregation": "SUM"}})


def test_csv_formula_protection():
    result = execute([record("1", title="=1+1")], Query())
    assert "'=1+1" in csv_result(result, Query())

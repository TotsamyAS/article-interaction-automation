from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from app.catalog import build_catalog
from app.dataset import generate_dataset, dataset_digest
from app.engine import execute


def test_all_fifteen_ground_truths_against_independent_selection(settings):
    records = generate_dataset(settings)
    d0 = settings.reference_date
    recent = lambda r, days: d0 - timedelta(days=days) <= r.created_at <= d0
    predicates = {
        "C1a": lambda r: r.status == "В работе" and r.priority == "Высокий",
        "C1b": lambda r: r.status == "На ревью" and r.priority == "Критический",
        "C1c": lambda r: r.status == "Открыта" and r.priority == "Средний",
        "C1d": lambda r: r.status == "Готово" and r.priority == "Низкий",
        "C1e": lambda r: r.status == "В работе" and r.priority == "Средний",
        "C2a": lambda r: r.epic == "Платежи" and r.status in ("В работе", "На ревью") and recent(r, 14),
        "C2b": lambda r: r.sprint == "Спринт 5" and r.status in ("Открыта", "В работе") and r.priority == "Высокий",
        "C2c": lambda r: "баг" in r.labels and r.status in ("В работе", "На ревью") and recent(r, 30),
        "C2d": lambda r: r.epic == "Авторизация" and r.status in ("Открыта", "В работе") and recent(r, 21),
        "C2e": lambda r: "фича" in r.labels and r.status in ("На ревью", "Готово") and recent(r, 7),
        "C3a": lambda r: r.status not in ("Отменена", "Готово") and r.deadline is not None and r.deadline < d0,
        "C3b": lambda r: r.assignee is not None and "баг" in r.labels and r.estimate_hours > 8,
        "C3c": lambda r: r.priority != "Низкий" and r.status == "Открыта" and r.deadline is None and r.assignee is not None,
        "C3d": lambda r: "документация" not in r.labels and r.status == "В работе" and r.estimate_hours > 4,
        "C3e": lambda r: r.created_at >= d0 - timedelta(days=60) and "рефакторинг" in r.labels and r.status == "На ревью",
    }
    catalog = build_catalog(d0)
    for task_id, predicate in predicates.items():
        selected = [r for r in records if predicate(r)]
        expected_value = None
        if task_id.startswith("C2"):
            if task_id[-1] in "ae":
                expected_value = Decimal(len(selected))
            else:
                expected_value = sum(Decimal(r.estimate_hours) for r in selected)
                if task_id.endswith("c"):
                    expected_value /= len(selected)
        elif task_id.startswith("C3"):
            field = {"a": "epic", "b": "sprint", "c": "assignee", "d": "epic", "e": "sprint"}[task_id[-1]]
            groups = defaultdict(list)
            for row in selected:
                groups[getattr(row, field)].append(row)
            scores = {key: sum(r.estimate_hours for r in group) if task_id[-1] in "bd" else len(group) for key, group in groups.items()}
            expected_value = Decimal(max(scores.values()))
            selected = [r for r in selected if scores[getattr(r, field)] == expected_value]
        actual = execute(records, catalog[task_id].query)
        assert actual.record_ids, task_id
        assert set(actual.record_ids) == {r.id for r in selected}, task_id
        assert (Decimal(actual.value) if actual.value is not None else None) == expected_value, task_id
    assert len(records) == len({r.id for r in records}) == 120
    assert dataset_digest(records) == dataset_digest(generate_dataset(settings))

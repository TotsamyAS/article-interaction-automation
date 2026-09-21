import hashlib
import json
import random
from datetime import timedelta

from .config import Settings
from .contracts import TaskRecord

STATUSES = ("Открыта", "В работе", "На ревью", "Готово", "Отменена")
PRIORITIES = ("Низкий", "Средний", "Высокий", "Критический")
EPICS = ("Авторизация", "Платежи", "Уведомления", "Профиль", "Инфраструктура")
LABELS = ("баг", "фича", "документация", "рефакторинг")


def generate_dataset(settings: Settings) -> list[TaskRecord]:
    rng = random.Random(settings.seed)
    d0 = settings.reference_date
    records = []
    for index in range(120):
        records.append(TaskRecord(
            id=f"TASK-{index + 1:03d}", title=f"Задача банковского приложения № {index + 1}",
            status=rng.choice(STATUSES), priority=rng.choice(PRIORITIES),
            assignee=None if index % 9 == 0 else f"Участник {rng.randint(1, 7)}",
            epic=rng.choice(EPICS), sprint=f"Спринт {rng.randint(1, 10)}",
            created_at=d0 - timedelta(days=round(index * 89 / 119)),
            deadline=None if index % 4 == 0 else d0 + timedelta(days=rng.randint(-30, 30)),
            labels=rng.sample(LABELS, rng.randint(1, 2)), estimate_hours=rng.randint(1, 40),
        ))
    # Anchors guarantee nonempty results for all fifteen experimental tasks.
    anchors = [
        dict(status="В работе", priority="Высокий", epic="Платежи", deadline=d0 - timedelta(days=1)),
        dict(status="На ревью", priority="Критический", labels=["баг"]),
        dict(status="Открыта", priority="Средний", deadline=None),
        dict(status="Готово", priority="Низкий"),
        dict(status="В работе", priority="Средний", epic="Авторизация", labels=["фича"], estimate_hours=12),
        dict(status="Открыта", priority="Высокий", sprint="Спринт 5", labels=["баг"], estimate_hours=16),
        dict(status="На ревью", priority="Средний", labels=["фича"]),
        dict(status="На ревью", priority="Средний", labels=["рефакторинг"]),
    ]
    for index, overrides in enumerate(anchors):
        data = records[index].model_dump()
        data.update(created_at=d0 - timedelta(days=index % 5), assignee=f"Участник {index % 7 + 1}")
        data.update(overrides)
        records[index] = TaskRecord.model_validate(data)
    return records


def dataset_digest(records: list[TaskRecord]) -> str:
    canonical = json.dumps([r.model_dump(mode="json") for r in records], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()

from dataclasses import dataclass
from datetime import date, timedelta

from .contracts import Filter, Grouping, Extremum, Output, Query


@dataclass(frozen=True)
class TaskDefinition:
    id: str
    level: int
    tci: int
    prompt: str
    query: Query
    training: bool = False


def build_catalog(d0: date) -> dict[str, TaskDefinition]:
    def f(field, operator, value=None):
        return Filter(field=field, operator=operator, value=value)

    def recent(days):
        return [f("created_at", "gte", (d0 - timedelta(days=days)).isoformat()),
                f("created_at", "lte", d0.isoformat())]

    tasks: dict[str, TaskDefinition] = {}

    def add(identifier, level, prompt, filters, aggregation=None, group=None, training=False):
        query = Query(
            filters=filters,
            grouping=Grouping(field=group, aggregation=aggregation,
                              aggregation_field="estimate_hours" if aggregation and aggregation != "COUNT" else None),
            extremum=Extremum(direction="max", metric=aggregation.lower()) if group else Extremum(),
            output=Output(format="csv", scope="extremum_group") if group else Output(),
        )
        tasks[identifier] = TaskDefinition(identifier, level, {1: 3, 2: 9, 3: 13}[level], prompt, query, training)

    for suffix, status, priority in [
        ("a", "В работе", "Высокий"), ("b", "На ревью", "Критический"),
        ("c", "Открыта", "Средний"), ("d", "Готово", "Низкий"), ("e", "В работе", "Средний"),
    ]:
        add(f"C1{suffix}", 1, f"Показать задачи со статусом «{status}» и приоритетом «{priority}».",
            [f("status", "eq", status), f("priority", "eq", priority)])
    add("C2a", 2, "Задачи в эпике «Платежи», статус «В работе» ИЛИ «На ревью», за последние 14 дней, посчитать количество.",
        [f("epic", "eq", "Платежи"), f("status", "in", ["В работе", "На ревью"]), *recent(14)], "COUNT")
    add("C2b", 2, "Задачи в спринте «Спринт 5», статус «Открыта» ИЛИ «В работе», приоритет «Высокий», посчитать суммарную оценку в часах.",
        [f("sprint", "eq", "Спринт 5"), f("status", "in", ["Открыта", "В работе"]), f("priority", "eq", "Высокий")], "SUM")
    add("C2c", 2, "Задачи с меткой «баг», статус «В работе» ИЛИ «На ревью», за последние 30 дней, посчитать среднюю оценку.",
        [f("labels", "eq", "баг"), f("status", "in", ["В работе", "На ревью"]), *recent(30)], "AVG")
    add("C2d", 2, "Задачи в эпике «Авторизация», статус «Открыта» ИЛИ «В работе», за последние 21 день, посчитать суммарную оценку в часах.",
        [f("epic", "eq", "Авторизация"), f("status", "in", ["Открыта", "В работе"]), *recent(21)], "SUM")
    add("C2e", 2, "Задачи с меткой «фича», статус «На ревью» ИЛИ «Готово», за последние 7 дней, посчитать количество.",
        [f("labels", "eq", "фича"), f("status", "in", ["На ревью", "Готово"]), *recent(7)], "COUNT")
    add("C3a", 3, "Исключить отменённые. Сгруппировать по эпикам, найти эпик с наибольшим числом просроченных (дедлайн прошёл, статус ≠ Готово), экспортировать.",
        [f("status", "neq", "Отменена"), f("deadline", "lt", d0.isoformat()), f("status", "neq", "Готово")], "COUNT", "epic")
    add("C3b", 3, "Исключить задачи без исполнителя. Сгруппировать по спринтам, найти спринт с наибольшей суммарной оценкой задач с меткой «баг» (оценка > 8 ч), экспортировать.",
        [f("assignee", "not_null"), f("labels", "eq", "баг"), f("estimate_hours", "gt", 8)], "SUM", "sprint")
    add("C3c", 3, "Исключить задачи с приоритетом «Низкий». Сгруппировать по исполнителям, найти исполнителя с наибольшим числом задач в статусе «Открыта» без дедлайна, экспортировать.",
        [f("priority", "neq", "Низкий"), f("status", "eq", "Открыта"), f("deadline", "is_null"), f("assignee", "not_null")], "COUNT", "assignee")
    add("C3d", 3, "Исключить задачи с меткой «документация». Сгруппировать по эпикам, найти эпик с наибольшей суммарной оценкой задач в статусе «В работе» (оценка > 4 ч), экспортировать.",
        [f("labels", "neq", "документация"), f("status", "eq", "В работе"), f("estimate_hours", "gt", 4)], "SUM", "epic")
    add("C3e", 3, "Исключить задачи, созданные более 60 дней назад. Сгруппировать по спринтам, найти спринт с наибольшим числом задач с меткой «рефакторинг» и статусом «На ревью», экспортировать.",
        [f("created_at", "gte", (d0 - timedelta(days=60)).isoformat()), f("labels", "eq", "рефакторинг"), f("status", "eq", "На ревью")], "COUNT", "sprint")
    add("TRAIN", 1, "Тренировка: показать задачи с приоритетом «Критический».",
        [f("priority", "eq", "Критический")], training=True)
    return tasks

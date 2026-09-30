import pytest

from app.catalog import build_catalog
from app.dataset import generate_dataset
from app.engine import execute
from app.errors import DomainError
from app.manual_query import compile_tags, suggestions


TASK_TAGS = {
    "C1a": ["Статус: В работе", "Приоритет: Высокий"],
    "C1b": ["Статус: На ревью", "Приоритет: Критический"],
    "C1c": ["Статус: Открыта", "Приоритет: Средний"],
    "C1d": ["Статус: Готово", "Приоритет: Низкий"],
    "C1e": ["Статус: В работе", "Приоритет: Средний"],
    "C2a": ["Эпик: Платежи", "Статус: В работе / На ревью", "Период: 14 дней", "Итог: количество"],
    "C2b": ["Спринт: Спринт 5", "Статус: Открыта / В работе", "Приоритет: Высокий", "Итог: сумма часов"],
    "C2c": ["Метка: баг", "Статус: В работе / На ревью", "Период: 30 дней", "Итог: средняя оценка"],
    "C2d": ["Эпик: Авторизация", "Статус: Открыта / В работе", "Период: 21 день", "Итог: сумма часов"],
    "C2e": ["Метка: фича", "Статус: На ревью / Готово", "Период: 7 дней", "Итог: количество"],
    "C3a": ["Статус != Отменена", "Дедлайн: просрочен", "Статус != Готово", "Группировка: эпик",
            "Итог: количество", "Экстремум: максимум", "Экспорт: CSV"],
    "C3b": ["Исполнитель: указан", "Метка: баг", "Оценка > 8", "Группировка: спринт",
            "Итог: сумма часов", "Экстремум: максимум", "Экспорт: CSV"],
    "C3c": ["Приоритет != Низкий", "Статус: Открыта", "Дедлайн: отсутствует", "Исполнитель: указан",
            "Группировка: исполнитель", "Итог: количество", "Экстремум: максимум", "Экспорт: CSV"],
    "C3d": ["Метка != документация", "Статус: В работе", "Оценка > 4", "Группировка: эпик",
            "Итог: сумма часов", "Экстремум: максимум", "Экспорт: CSV"],
    "C3e": ["Период: 60 дней", "Метка: рефакторинг", "Статус: На ревью", "Группировка: спринт",
            "Итог: количество", "Экстремум: максимум", "Экспорт: CSV"],
}


@pytest.mark.parametrize("task_id", sorted(TASK_TAGS))
def test_manual_tags_reproduce_canonical_task_result(settings, task_id):
    records = generate_dataset(settings)
    catalog = build_catalog(settings.reference_date)
    manual_query = compile_tags(TASK_TAGS[task_id], records, settings.reference_date)
    manual = execute(records, manual_query)
    expected = execute(records, catalog[task_id].query)
    assert manual.record_ids == expected.record_ids
    assert manual.value == expected.value
    assert manual.selected_groups == expected.selected_groups
    assert manual_query.output.format == catalog[task_id].query.output.format


def test_manual_tag_suggestions_cover_required_operation_shapes(settings):
    available = suggestions(generate_dataset(settings))
    for expected in ("Период: 14 дней", "Дедлайн: просрочен", "Исполнитель: указан",
                     "Итог: количество", "Группировка: направление работ", "Экстремум: максимум", "Экспорт: CSV"):
        assert expected in available


def test_manual_tags_reject_unknown_values(settings):
    records = generate_dataset(settings)
    with pytest.raises(DomainError, match="допустим"):
        compile_tags(["Статус: Несуществующий"], records, settings.reference_date)


def test_linked_builder_vocabulary_compiles_to_shared_query(client):
    help_data = client.get("/api/manual-query").json()
    options = {item["label"]: item for item in help_data["builders"]}
    assert "В работе" in options["Статус"]["values"]
    assert "На ревью" in options["Статус"]["values"]
    assert options["Статус"]["alternatives"]
    assert options["Исполнитель"]["exclusive_values"] == ["отсутствует", "указан"]
    assert options["Экспорт"]["kind"] == "action"
    response = client.post("/api/manual-query/compile", json={"tags": [
        "Статус: В работе / На ревью", "Приоритет != Низкий", "Итог: количество"
    ]})
    assert response.status_code == 200
    query = response.json()
    assert query["filters"][0]["operator"] == "in"
    assert query["filters"][0]["value"] == ["В работе", "На ревью"]
    assert query["filters"][1]["logic"] == "AND"
    assert query["filters"][1]["operator"] == "neq"
    assert query["grouping"]["aggregation"] == "COUNT"


@pytest.mark.parametrize('task_id', sorted(TASK_TAGS))
def test_new_project_terms_preserve_all_fifteen_canonical_answers(settings, task_id):
    from app.terminology import display_text
    records = generate_dataset(settings)
    original = compile_tags(TASK_TAGS[task_id], records, settings.reference_date)
    renamed = compile_tags([display_text(tag) for tag in TASK_TAGS[task_id]], records, settings.reference_date)
    assert renamed == original


def test_builder_exposes_new_terms_and_legacy_field_aliases(client):
    options = {item['label']: item for item in client.get('/api/manual-query').json()['builders']}
    assert 'Эпик' not in options and 'Спринт' not in options
    assert options['Направление работ']['aliases'] == ['Эпик']
    assert options['Рабочий цикл']['aliases'] == ['Спринт']
    assert 'Рабочий цикл 5' in options['Рабочий цикл']['values']
    assert 'Дедлайн' in options
    prompts = [task['prompt'] for task in client.get('/api/tasks').json()]
    assert not any('эпик' in prompt.lower() or 'спринт' in prompt.lower() for prompt in prompts)

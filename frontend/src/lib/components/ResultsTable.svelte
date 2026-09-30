<script lang="ts">
  import type { FieldName, Query, QueryResult } from '../types';
  import { displayValue } from '../terminology';
  let { result, query = null }: { result: QueryResult; query?: Query | null } = $props();

  const fieldLabels: Record<FieldName, string> = {
    id: 'ID', title: 'Название', status: 'Статус', priority: 'Приоритет', assignee: 'Исполнитель',
    epic: 'Направление работ', sprint: 'Рабочий цикл', created_at: 'Создана', deadline: 'Дедлайн',
    labels: 'Метки', estimate_hours: 'Часы'
  };
  const aggregationLabels: Record<string, string> = {
    COUNT: 'Количество', SUM: 'Сумма часов', AVG: 'Средняя оценка', MAX: 'Максимальная оценка', MIN: 'Минимальная оценка'
  };
  function header(field: FieldName) {
    const arrow = query?.sorting.field === field ? (query.sorting.direction === 'desc' ? ' ↓' : ' ↑') : '';
    return fieldLabels[field] + arrow;
  }
</script>

<section class="result-panel" aria-live="polite">
  <div class="result-panel-heading">
    <div><h3>Предпросмотр результата</h3><p class="muted small">Это данные после текущих фильтров и операций.</p></div>
    {#if query?.sorting.field}<span class="result-state">Сортировка: {fieldLabels[query.sorting.field]} {query.sorting.direction === 'desc' ? '↓' : '↑'}</span>{/if}
  </div>
  <div class="result-summary">
    <div><span class="summary-label">Строк в результате</span><strong>{result.record_ids.length}</strong></div>
    {#if result.value !== null}<div><span class="summary-label">Итог</span><strong>{result.value}</strong></div>{/if}
    {#if result.selected_groups.length}<div><span class="summary-label">Выбранная группа</span><strong>{result.selected_groups.map((value) => value === null ? 'Без значения' : displayValue(value)).join(', ')}</strong></div>{/if}
  </div>

  {#if result.groups.length}
    <div class="group-preview">
      <div class="section-heading"><h4>Группы после фильтрации</h4><span class="muted small">Сначала сравнивайте эту таблицу, затем строки ниже.</span></div>
      {#if query?.grouping.field && !query.grouping.aggregation}
        <p class="notice warning">Группировка уже разбила задачи на группы, но «Итог» не выбран. Чтобы найти самую большую/маленькую группу, добавьте «Итог: Количество», затем «Экстремум».</p>
      {/if}
      <div class="table-wrap compact-table">
        <table class="groups-table">
          <thead><tr><th>Группа</th><th>Задач в группе</th><th>{query?.grouping.aggregation ? aggregationLabels[query.grouping.aggregation] : 'Итог'}</th><th>Результат экстремума</th></tr></thead>
          <tbody>{#each result.groups as group (String(group.key))}<tr class:selected-group={result.selected_groups.includes(group.key)}>
            <td><strong>{group.key === null ? 'Без значения' : displayValue(group.key)}</strong></td>
            <td>{group.record_ids.length}</td><td>{group.value ?? '—'}</td>
            <td>{result.selected_groups.includes(group.key) ? '✓ выбрана' : '—'}</td>
          </tr>{/each}</tbody>
        </table>
      </div>
    </div>
  {/if}

  {#if result.tie}<p class="notice warning">Несколько групп имеют одинаковое экстремальное значение, поэтому в результат вошли все такие группы.</p>{/if}
  {#if result.records.length}
    <h4 class="records-heading">Строки результата</h4>
    <div class="table-wrap">
      <table>
        <thead><tr><th>{header('id')}</th><th>{header('title')}</th><th>{header('status')}</th><th>{header('priority')}</th><th>{header('assignee')}</th><th>{header('epic')}</th><th>{header('sprint')}</th><th>{header('created_at')}</th><th>{header('deadline')}</th><th>Метки</th><th>{header('estimate_hours')}</th></tr></thead>
        <tbody>{#each result.records as record (record.id)}<tr>
          <td class="nowrap">{record.id}</td><td>{record.title}</td><td>{record.status}</td><td>{record.priority}</td>
          <td>{record.assignee ?? '—'}</td><td>{record.epic}</td><td>{displayValue(record.sprint)}</td><td class="nowrap">{record.created_at}</td>
          <td class="nowrap">{record.deadline ?? '—'}</td><td>{record.labels.join(', ')}</td><td>{record.estimate_hours}</td>
        </tr>{/each}</tbody>
      </table>
    </div>
  {:else}
    <p class="empty-state">В выборке нет записей. Проверьте фильтры выше: возможно, их сочетание исключило все задачи.</p>
  {/if}
</section>

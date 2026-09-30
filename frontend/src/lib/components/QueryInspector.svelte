<script lang="ts">
  import type { FieldName, Query } from '../types';
  import { displayValue } from '../terminology';

  let { query }: { query: Query } = $props();
  let open = $state(false);

  const fieldLabels: Record<FieldName, string> = {
    id: 'ID', title: 'Название', status: 'Статус', priority: 'Приоритет', assignee: 'Исполнитель',
    epic: 'Направление работ', sprint: 'Рабочий цикл', created_at: 'Дата создания', deadline: 'Дедлайн',
    labels: 'Метка', estimate_hours: 'Оценка, часы'
  };
  const operatorLabels: Record<string, string> = {
    eq: '=', neq: '≠', gt: '>', lt: '<', gte: '≥', lte: '≤', in: 'один из', not_in: 'не входит в',
    is_null: 'отсутствует', not_null: 'указан'
  };
  const aggregationLabels: Record<string, string> = {
    COUNT: 'Количество', SUM: 'Сумма часов', AVG: 'Средняя оценка', MAX: 'Максимальная оценка', MIN: 'Минимальная оценка'
  };

  function valueText(value: unknown): string {
    if (value === null || value === undefined) return '—';
    if (Array.isArray(value)) return value.map((item) => displayValue(String(item))).join(', ');
    return displayValue(String(value));
  }
</script>

<section class="query-inspector">
  <button type="button" class="secondary compact" data-track="query-inspector-toggle" onclick={() => open = !open}>
    {open ? 'Скрыть выполненный запрос' : 'Показать выполненный запрос'}
  </button>
  {#if open}
    <div class="query-inspector-body">
      <h4>Что именно выполнил стенд</h4>
      <dl class="query-summary-list">
        <dt>Фильтры</dt>
        <dd>{query.filters.length ? query.filters.map((filter) => `${fieldLabels[filter.field]} ${operatorLabels[filter.operator] ?? filter.operator}${['is_null', 'not_null'].includes(filter.operator) ? '' : ` ${valueText(filter.value)}`}`).join(' · И · ') : 'нет'}</dd>
        <dt>Группировка</dt><dd>{query.grouping.field ? fieldLabels[query.grouping.field] : 'нет'}</dd>
        <dt>Итог</dt><dd>{query.grouping.aggregation ? aggregationLabels[query.grouping.aggregation] : 'нет'}</dd>
        <dt>Экстремум</dt><dd>{query.extremum.direction === 'max' ? 'Максимум' : query.extremum.direction === 'min' ? 'Минимум' : 'нет'}</dd>
        <dt>Сортировка</dt><dd>{query.sorting.field ? `${fieldLabels[query.sorting.field]}, ${query.sorting.direction === 'desc' ? 'по убыванию' : 'по возрастанию'}` : 'нет'}</dd>
        <dt>Вывод</dt><dd>{query.output.format.toUpperCase()} · {query.output.scope === 'extremum_group' ? 'только выбранная экстремальная группа' : query.output.scope === 'aggregate_only' ? 'только агрегат' : 'все строки результата'}</dd>
      </dl>
      <details>
        <summary>Технический Query (JSON)</summary>
        <pre class="query-json">{JSON.stringify(query, null, 2)}</pre>
      </details>
    </div>
  {/if}
</section>

<script lang="ts">
  import { api, errorMessage } from '../api';
  import { submitTrialAttempt } from '../trial-actions';
  import type { AttemptView, FieldName, Query, QueryFilter, QueryResult, TaskRecord, TrialView } from '../types';
  import type { TrialEventLogger } from '../event-logger';
  import ResultsTable from './ResultsTable.svelte';

  let { trial, records, referenceDate, logger, onAttempt, onBusy }: {
    trial: TrialView; records: TaskRecord[]; referenceDate: string; logger: TrialEventLogger;
    onAttempt: (attempt: AttemptView) => void; onBusy: (busy: boolean, text?: string) => void;
  } = $props();

  type DraftOperator = 'eq' | 'neq' | 'gt' | 'lt' | 'gte' | 'lte' | 'is_null' | 'not_null' | 'recent' | 'overdue';
  interface FilterDraft { id: string; field: FieldName; operator: DraftOperator; values: string[]; value: string }

  const fields: Array<{ value: FieldName; label: string }> = [
    { value: 'status', label: 'Статус' }, { value: 'priority', label: 'Приоритет' },
    { value: 'assignee', label: 'Исполнитель' }, { value: 'epic', label: 'Эпик' },
    { value: 'sprint', label: 'Спринт' }, { value: 'labels', label: 'Метка' },
    { value: 'created_at', label: 'Дата создания' }, { value: 'deadline', label: 'Дедлайн' },
    { value: 'estimate_hours', label: 'Оценка, часы' }
  ];
  const categorical = new Set<FieldName>(['status', 'priority', 'assignee', 'epic', 'sprint', 'labels']);
  let filters = $state<FilterDraft[]>([newFilter()]);
  let groupField = $state<Query['grouping']['field']>(null);
  let aggregation = $state<Query['grouping']['aggregation']>(null);
  let extremum = $state<Query['extremum']['direction']>(null);
  let sortField = $state<FieldName | null>(null);
  let sortDirection = $state<'asc' | 'desc'>('asc');
  let outputFormat = $state<'table' | 'csv'>('table');
  let result = $state<QueryResult | null>(null);
  let message = $state('');
  let busy = $state(false);
  let retryId = $state<string | null>(null);
  let retryQuery = $state('');

  function newFilter(): FilterDraft { return { id: crypto.randomUUID(), field: 'status', operator: 'eq', values: [], value: '' }; }
  function options(field: FieldName) {
    const values = new Set<string>();
    for (const record of records) {
      const raw = record[field as keyof TaskRecord];
      if (field === 'labels') for (const label of record.labels) values.add(label);
      else if (raw !== null && raw !== undefined && typeof raw !== 'object') values.add(String(raw));
    }
    return [...values].sort((a, b) => a.localeCompare(b, 'ru'));
  }
  function operators(field: FieldName): Array<{ value: DraftOperator; label: string }> {
    if (categorical.has(field)) return [
      { value: 'eq', label: 'равно / один из' }, { value: 'neq', label: 'не равно / ни один из' },
      ...(field === 'assignee' ? [{ value: 'is_null' as const, label: 'не указан' }, { value: 'not_null' as const, label: 'указан' }] : [])
    ];
    if (field === 'created_at') return [
      { value: 'recent', label: 'за последние N дней' }, { value: 'gte', label: 'не раньше' }, { value: 'lte', label: 'не позже' }
    ];
    if (field === 'deadline') return [
      { value: 'overdue', label: 'просрочен' }, { value: 'is_null', label: 'отсутствует' }, { value: 'not_null', label: 'указан' },
      { value: 'gte', label: 'не раньше' }, { value: 'lte', label: 'не позже' }
    ];
    return [
      { value: 'eq', label: '=' }, { value: 'neq', label: '≠' }, { value: 'gt', label: '>' },
      { value: 'gte', label: '≥' }, { value: 'lt', label: '<' }, { value: 'lte', label: '≤' }
    ];
  }
  function changeField(filter: FilterDraft, field: FieldName) {
    filter.field = field; filter.values = []; filter.value = '';
    filter.operator = field === 'created_at' ? 'recent' : field === 'deadline' ? 'overdue' : 'eq';
  }
  function isoMinusDays(days: number) {
    const date = new Date(`${referenceDate}T00:00:00Z`);
    date.setUTCDate(date.getUTCDate() - days);
    return date.toISOString().slice(0, 10);
  }
  function buildQuery(): Query {
    const queryFilters: QueryFilter[] = [];
    for (const filter of filters) {
      if (filter.operator === 'recent') {
        const days = Number(filter.value);
        if (!Number.isInteger(days) || days <= 0) throw new Error('Для периода укажите положительное число дней.');
        queryFilters.push({ field: 'created_at', operator: 'gte', value: isoMinusDays(days) }, { field: 'created_at', operator: 'lte', value: referenceDate });
      } else if (filter.operator === 'overdue') {
        queryFilters.push({ field: 'deadline', operator: 'lt', value: referenceDate });
      } else if (filter.operator === 'is_null' || filter.operator === 'not_null') {
        queryFilters.push({ field: filter.field, operator: filter.operator, value: null });
      } else if (categorical.has(filter.field)) {
        if (!filter.values.length) throw new Error(`Выберите значение для поля «${fields.find((item) => item.value === filter.field)?.label}».`);
        const multi = filter.values.length > 1;
        const operator = multi ? (filter.operator === 'neq' ? 'not_in' : 'in') : filter.operator;
        queryFilters.push({ field: filter.field, operator: operator as QueryFilter['operator'], value: multi ? filter.values : filter.values[0] });
      } else {
        if (!filter.value) throw new Error('Заполните значение фильтра.');
        queryFilters.push({ field: filter.field, operator: filter.operator as QueryFilter['operator'], value: filter.field === 'estimate_hours' ? Number(filter.value) : filter.value });
      }
    }
    if (extremum && (!groupField || !aggregation)) throw new Error('Для экстремума выберите группировку и итог.');
    return {
      filters: queryFilters,
      grouping: { field: groupField, aggregation, aggregation_field: aggregation && aggregation !== 'COUNT' ? 'estimate_hours' : null },
      extremum: { direction: extremum, metric: extremum && aggregation ? aggregation.toLowerCase() as Query['extremum']['metric'] : null },
      sorting: { field: sortField, direction: sortField ? sortDirection : null },
      output: { format: outputFormat, scope: extremum ? 'extremum_group' : 'all_records' }
    };
  }
  async function preview() {
    message = ''; busy = true; onBusy(true, 'Применяем фильтры…');
    try {
      const query = buildQuery();
      result = await logger.measure('m1-preview', () => api<QueryResult>(`/api/trials/${trial.id}/preview`, { method: 'POST', body: JSON.stringify(query) }));
      await logger.flushSafely();
    } catch (error) { message = errorMessage(error); }
    finally { busy = false; onBusy(false); }
  }
  async function submit() {
    message = ''; busy = true; onBusy(true, 'Проверяем результат…');
    try {
      const query = buildQuery();
      const serialized = JSON.stringify(query);
      if (!retryId || retryQuery !== serialized) { retryId = crypto.randomUUID(); retryQuery = serialized; }
      const attempt = await submitTrialAttempt(trial, query, logger, retryId);
      retryId = null; retryQuery = '';
      result = attempt.result;
      if (attempt.export_url) {
        const link = document.createElement('a'); link.href = attempt.export_url; link.download = 'result.csv'; link.click();
      }
      onAttempt(attempt);
    } catch (error) { message = errorMessage(error); }
    finally { busy = false; onBusy(false); }
  }
</script>

<div class="workbench" data-track="m1-workbench">
  <div class="workbench-heading"><div><span class="mode-pill">M1 · GUI</span><h2>Ручная обработка</h2></div></div>
  <p class="muted">Добавьте фильтры, примените их, при необходимости задайте группировку, сортировку или CSV. Когда результат готов — нажмите «Проверить результат».</p>

  <section class="control-card">
    <div class="section-heading"><h3>Фильтры</h3><button type="button" class="secondary compact" data-track="m1-add-filter" onclick={() => filters.push(newFilter())}>+ Добавить</button></div>
    <div class="filter-list">
      {#each filters as filter (filter.id)}
        <div class="filter-row">
          <select aria-label="Поле фильтра" value={filter.field} onchange={(event) => changeField(filter, event.currentTarget.value as FieldName)}>
            {#each fields as field}<option value={field.value}>{field.label}</option>{/each}
          </select>
          <select aria-label="Оператор" bind:value={filter.operator}>{#each operators(filter.field) as operator}<option value={operator.value}>{operator.label}</option>{/each}</select>
          {#if categorical.has(filter.field) && !['is_null', 'not_null'].includes(filter.operator)}
            <select class="multi-select" multiple aria-label="Значения фильтра" bind:value={filter.values}>
              {#each options(filter.field) as value}<option {value}>{value}</option>{/each}
            </select>
          {:else if filter.operator === 'recent'}
            <input type="number" min="1" step="1" placeholder="14" aria-label="Количество дней" bind:value={filter.value} />
          {:else if !['is_null', 'not_null', 'overdue'].includes(filter.operator)}
            <input type={filter.field === 'estimate_hours' ? 'number' : 'date'} min={filter.field === 'estimate_hours' ? '0' : undefined}
              aria-label="Значение фильтра" bind:value={filter.value} />
          {:else}<span class="filter-fixed">Значение не требуется</span>{/if}
          <button type="button" class="icon-button" aria-label="Удалить фильтр" disabled={filters.length === 1} onclick={() => filters = filters.filter((item) => item.id !== filter.id)}>×</button>
        </div>
      {/each}
    </div>
    <button type="button" class="secondary" data-track="m1-preview" disabled={busy} onclick={preview}>Применить фильтры</button>
  </section>

  <section class="control-card operation-grid">
    <label>Группировка<select bind:value={groupField}><option value={null}>Нет</option><option value="epic">Эпик</option><option value="sprint">Спринт</option><option value="assignee">Исполнитель</option><option value="status">Статус</option><option value="priority">Приоритет</option></select></label>
    <label>Итог<select bind:value={aggregation}><option value={null}>Нет</option><option value="COUNT">Количество</option><option value="SUM">Сумма часов</option><option value="AVG">Средняя оценка</option><option value="MAX">Максимальная оценка</option><option value="MIN">Минимальная оценка</option></select></label>
    <label>Экстремум<select bind:value={extremum}><option value={null}>Нет</option><option value="max">Максимум</option><option value="min">Минимум</option></select></label>
    <label>Сортировка<select bind:value={sortField}><option value={null}>Нет</option>{#each fields.filter((field) => field.value !== 'labels') as field}<option value={field.value}>{field.label}</option>{/each}</select></label>
    <label>Направление<select bind:value={sortDirection} disabled={!sortField}><option value="asc">По возрастанию</option><option value="desc">По убыванию</option></select></label>
    <label>Формат<select bind:value={outputFormat}><option value="table">Таблица</option><option value="csv">CSV</option></select></label>
  </section>

  {#if message}<p class="notice error" role="alert">{message}</p>{/if}
  <div class="primary-actions"><button type="button" class="primary" data-track="m1-submit" disabled={busy} onclick={submit}>Проверить результат</button></div>
  {#if result}<ResultsTable {result} />{/if}
</div>

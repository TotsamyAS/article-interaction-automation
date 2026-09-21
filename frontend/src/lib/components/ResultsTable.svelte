<script lang="ts">
  import type { QueryResult } from '../types';
  let { result }: { result: QueryResult } = $props();
</script>

<section class="result-panel" aria-live="polite">
  <div class="result-summary">
    <div><span class="summary-label">Найдено</span><strong>{result.record_ids.length}</strong></div>
    {#if result.value !== null}<div><span class="summary-label">Итог</span><strong>{result.value}</strong></div>{/if}
    {#if result.selected_groups.length}<div><span class="summary-label">Выбранная группа</span><strong>{result.selected_groups.map((value) => value ?? 'Без значения').join(', ')}</strong></div>{/if}
  </div>
  {#if result.tie}<p class="notice warning">Несколько групп имеют одинаковое экстремальное значение.</p>{/if}
  {#if result.records.length}
    <div class="table-wrap">
      <table>
        <thead><tr><th>ID</th><th>Название</th><th>Статус</th><th>Приоритет</th><th>Исполнитель</th><th>Эпик</th><th>Спринт</th><th>Создана</th><th>Дедлайн</th><th>Метки</th><th>Часы</th></tr></thead>
        <tbody>{#each result.records as record (record.id)}<tr>
          <td class="nowrap">{record.id}</td><td>{record.title}</td><td>{record.status}</td><td>{record.priority}</td>
          <td>{record.assignee ?? '—'}</td><td>{record.epic}</td><td>{record.sprint}</td><td class="nowrap">{record.created_at}</td>
          <td class="nowrap">{record.deadline ?? '—'}</td><td>{record.labels.join(', ')}</td><td>{record.estimate_hours}</td>
        </tr>{/each}</tbody>
      </table>
    </div>
  {:else}
    <p class="empty-state">В выборке нет записей.</p>
  {/if}
</section>

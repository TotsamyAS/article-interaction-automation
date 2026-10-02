<script lang="ts">
  import { ApiError, workbenchError } from '../api';
  import { previewTextRequest, submitM3Attempt } from '../trial-actions';
  import type { AttemptView, Query, QueryResult, TrialView } from '../types';
  import type { TrialEventLogger } from '../event-logger';
  import QueryInspector from './QueryInspector.svelte';
  import ResultsTable from './ResultsTable.svelte';

  let { trial, logger, onAttempt, onBusy, validationTaskId }: {
    trial: TrialView; logger: TrialEventLogger;
    onAttempt: (attempt: AttemptView) => void; onBusy: (busy: boolean, text?: string) => void;
    validationTaskId?: string;
  } = $props();

  let text = $state('');
  let result = $state<QueryResult | null>(null);
  let previewQuery = $state<Query | null>(null);
  let previewText = $state('');
  let requestId = $state<string | null>(null);
  let requestText = $state('');
  let message = $state('');
  let busy = $state(false);
  const previewCurrent = $derived(Boolean(previewQuery && requestId) && text.trim() === previewText && requestText === previewText);

  async function preview() {
    const value = text.trim();
    message = '';
    if (!value) { message = 'Опишите требуемый результат. Укажите фильтры, а если нужны группы — что считать и какой максимум/минимум выбрать.'; return; }
    if (!requestId || requestText !== value) { requestId = crypto.randomUUID(); requestText = value; }
    busy = true; onBusy(true, 'RouterAI интерпретирует запрос для предпросмотра…');
    try {
      const next = await previewTextRequest(trial, value, logger, requestId, 'm3-preview', validationTaskId);
      previewQuery = next.query; result = next.result; previewText = value;
    } catch (error) {
      message = workbenchError(error);
      if (error instanceof ApiError) { requestId = null; requestText = ''; }
    } finally { busy = false; onBusy(false); }
  }

  async function submit() {
    message = '';
    if (!previewCurrent || !requestId) { message = 'Сначала получите актуальный предпросмотр. Итоговая кнопка засчитывает именно показанный результат и не запускает RouterAI повторно.'; return; }
    busy = true; onBusy(true, 'Фиксируем показанный предпросмотр как итоговый ответ…');
    try {
      const attempt = await submitM3Attempt(trial, previewText, logger, requestId, validationTaskId);
      result = attempt.result;
      onAttempt(attempt);
    } catch (error) { message = workbenchError(error); }
    finally { busy = false; onBusy(false); }
  }
</script>

<div class="workbench" data-track="m3-workbench">
  <div class="workbench-heading"><div><span class="mode-pill">M3 · Text</span><h2>Запрос на естественном языке</h2></div></div>
  <p class="instruction">Опишите своими словами конечный результат. Сначала получите предпросмотр: RouterAI преобразует текст в структурированный Query, а общий executor покажет таблицу. Предпросмотр не расходует попытку.</p>
  <textarea data-track="m3-text" rows="5" maxlength="2000" aria-label="Текст запроса" placeholder="Например: отфильтруйте задачи, сгруппируйте их, посчитайте нужный итог и при необходимости выберите максимум/минимум и экспорт" bind:value={text}></textarea>
  {#if message}<p class="notice error" role="alert"><strong>Не удалось выполнить действие.</strong> {message}</p>{/if}
  {#if previewQuery && !previewCurrent}<p class="notice warning">Текст изменён после предпросмотра. Нижняя таблица относится к предыдущей формулировке — обновите предпросмотр.</p>{/if}
  <div class="preview-actions">
    <button type="button" class="secondary" data-track="m3-preview" disabled={busy || !text.trim()} onclick={preview}>{result ? 'Обновить предпросмотр' : 'Показать предпросмотр'}</button>
    <button type="button" class="primary" data-track="m3-submit" disabled={busy || !previewCurrent} onclick={submit}>Отправить итоговый ответ</button>
  </div>
  {#if previewQuery}<QueryInspector query={previewQuery} />{/if}
  {#if result}<ResultsTable {result} query={previewQuery} />{:else}<p class="preview-placeholder">После предпросмотра здесь появится таблица и кнопка показа фактически выполненного Query.</p>{/if}
</div>

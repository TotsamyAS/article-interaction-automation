<script lang="ts">
  import { ApiError, workbenchError } from '../api';
  import { previewTextRequest, reuseTaskPromptAttempt, submitM5Attempt } from '../trial-actions';
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
    const value = text.trim(); message = '';
    if (!value) { message = 'Опишите конечную цель для агента: какие задачи оставить, какие операции выполнить и нужен ли экспорт.'; return; }
    if (!requestId || requestText !== value) { requestId = crypto.randomUUID(); requestText = value; }
    busy = true; onBusy(true, 'Агент RouterAI выполняет инструменты для предпросмотра…');
    try {
      const next = await previewTextRequest(trial, value, logger, requestId, 'm5-preview', validationTaskId);
      previewQuery = next.query; result = next.result; previewText = value;
    } catch (error) {
      message = workbenchError(error);
      if (error instanceof ApiError) { requestId = null; requestText = ''; }
    } finally { busy = false; onBusy(false); }
  }

  async function reuseCurrentPrompt() {
    message = '';
    busy = true; onBusy(true, 'Переиспользуем текст задания как цель и сразу засчитываем попытку…');
    try {
      const attempt = await reuseTaskPromptAttempt(trial, logger, 'M5', validationTaskId);
      result = attempt.result;
      previewQuery = null;
      onAttempt(attempt);
    } catch (error) { message = workbenchError(error); }
    finally { busy = false; onBusy(false); }
  }

  async function submit() {
    message = '';
    if (!previewCurrent || !requestId) { message = 'Сначала получите актуальный предпросмотр. Итоговая кнопка засчитывает уже выполненную агентом траекторию и показанный Query, не запускает агента второй раз.'; return; }
    busy = true; onBusy(true, 'Фиксируем показанный результат агента как итоговый ответ…');
    try {
      const attempt = await submitM5Attempt(trial, previewText, logger, requestId, validationTaskId);
      result = attempt.result;
      onAttempt(attempt);
    } catch (error) { message = workbenchError(error); }
    finally { busy = false; onBusy(false); }
  }
</script>

<div class="workbench" data-track="m5-workbench">
  <div class="workbench-heading"><div><span class="mode-pill">M5 · Agent</span><h2>Цель для агента</h2></div></div>
  <p class="instruction">Опишите конечную цель. В предпросмотре агент RouterAI/DeepSeek один раз выполнит последовательность атомарных инструментов; вы увидите итоговую таблицу и Query. Только после проверки отправьте этот же результат как итоговый ответ.</p>
  <textarea data-track="m5-text" rows="5" maxlength="2000" aria-label="Текст цели" placeholder="Опишите результат, который должен получить агент" bind:value={text}></textarea>
  {#if message}<p class="notice error" role="alert"><strong>Не удалось выполнить действие.</strong> {message}</p>{/if}
  {#if previewQuery && !previewCurrent}<p class="notice warning">Цель изменена после предпросмотра. Нижняя таблица относится к предыдущему запуску агента — обновите предпросмотр.</p>{/if}
  <div class="preview-actions reuse-prompt-actions">
    <button type="button" class="secondary" data-track="m5-reuse-prompt" disabled={busy} onclick={reuseCurrentPrompt}>Переиспользовать текущий промпт</button>
    <span class="muted small">Сразу засчитывается как попытка. Повтор того же текста не увеличивает счётчик разных попыток.</span>
  </div>
  <div class="preview-actions">
    <button type="button" class="secondary" data-track="m5-preview" disabled={busy || !text.trim()} onclick={preview}>{result ? 'Обновить предпросмотр' : 'Показать предпросмотр'}</button>
    <button type="button" class="primary" data-track="m5-submit" disabled={busy || !previewCurrent} onclick={submit}>Отправить итоговый ответ</button>
  </div>
  {#if previewQuery}<QueryInspector query={previewQuery} />{/if}
  {#if result}<ResultsTable {result} query={previewQuery} />{:else}<p class="preview-placeholder">После предпросмотра здесь появится итоговая выборка агента и фактически выполненный Query.</p>{/if}
</div>

<script lang="ts">
  import { ApiError, errorMessage } from '../api';
  import { submitM3Attempt } from '../trial-actions';
  import type { AttemptView, QueryResult, TrialView } from '../types';
  import type { TrialEventLogger } from '../event-logger';
  import ResultsTable from './ResultsTable.svelte';

  let { trial, logger, onAttempt, onBusy }: {
    trial: TrialView; logger: TrialEventLogger;
    onAttempt: (attempt: AttemptView) => void; onBusy: (busy: boolean, text?: string) => void;
  } = $props();

  let text = $state('');
  let result = $state<QueryResult | null>(null);
  let message = $state('');
  let busy = $state(false);
  let retryId = $state<string | null>(null);
  let retryText = $state('');

  async function submit() {
    const requestText = text.trim();
    message = '';
    if (!requestText) { message = 'Опишите требуемый результат.'; return; }
    busy = true; onBusy(true, 'Интерпретируем запрос…');
    if (!retryId || retryText !== requestText) { retryId = crypto.randomUUID(); retryText = requestText; }
    try {
      const attempt = await submitM3Attempt(trial, requestText, logger, retryId);
      retryId = null; retryText = '';
      result = attempt.result;
      if (attempt.export_url) {
        const link = document.createElement('a'); link.href = attempt.export_url; link.download = 'result.csv'; link.click();
      }
      onAttempt(attempt);
    } catch (error) {
      message = errorMessage(error);
      // A server response means the request outcome is known. Next click is a new LLM call.
      // Keep the id only for an indeterminate browser/network failure.
      if (error instanceof ApiError) { retryId = null; retryText = ''; }
    } finally { busy = false; onBusy(false); }
  }
</script>

<div class="workbench" data-track="m3-workbench">
  <div class="workbench-heading"><div><span class="mode-pill">M3 · Text</span><h2>Запрос на естественном языке</h2></div></div>
  <p class="instruction">Опишите своими словами, какой результат нужно получить. Система преобразует формулировку в структурированный запрос и выполнит его тем же модулем, что используется в M2.</p>
  <textarea data-track="m3-text" rows="5" maxlength="2000" aria-label="Текст запроса" placeholder="Например: найдите нужные задачи, при необходимости укажите подсчёт, группировку или экспорт" bind:value={text}></textarea>
  {#if message}<p class="notice error" role="alert">{message}</p>{/if}
  <div class="primary-actions"><button type="button" class="primary" data-track="m3-submit" disabled={busy || !text.trim()} onclick={submit}>Выполнить</button></div>
  {#if result}<ResultsTable {result} />{/if}
</div>

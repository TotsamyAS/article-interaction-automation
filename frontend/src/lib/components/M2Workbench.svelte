<script lang="ts">
  import { api, errorMessage } from '../api';
  import { submitTrialAttempt } from '../trial-actions';
  import type { AttemptView, ManualQueryHelp, Query, QueryResult, TrialView } from '../types';
  import type { TrialEventLogger } from '../event-logger';
  import TagInput from './TagInput.svelte';
  import ResultsTable from './ResultsTable.svelte';

  let { trial, help, logger, onAttempt, onBusy }: {
    trial: TrialView; help: ManualQueryHelp; logger: TrialEventLogger;
    onAttempt: (attempt: AttemptView) => void; onBusy: (busy: boolean, text?: string) => void;
  } = $props();

  let tags = $state<string[]>([]);
  let pendingTag = $state(false);
  let result = $state<QueryResult | null>(null);
  let message = $state('');
  let busy = $state(false);
  let retryId = $state<string | null>(null);
  let retryQuery = $state('');

  async function submit() {
    message = '';
    if (!tags.length || pendingTag) { message = 'Завершите текущее условие или отмените его ввод.'; return; }
    busy = true; onBusy(true, 'Выполняем структурированный запрос…');
    try {
      const query = await logger.measure('m2-compile', () => api<Query>('/api/manual-query/compile', {
        method: 'POST', body: JSON.stringify({ tags })
      }));
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

<div class="workbench" data-track="m2-workbench">
  <div class="workbench-heading"><div><span class="mode-pill">M2 · Form</span><h2>Конструктор запроса</h2></div></div>
  <p class="instruction">{help.instruction}</p>
  <TagInput bind:value={tags} bind:pending={pendingTag} builders={help.builders} placeholder={help.placeholder} disabled={busy} maxTags={40} />
  <div class="syntax-hints"><span>Примеры синтаксиса:</span>{#each help.examples as example}<code>{example}</code>{/each}</div>
  {#if message}<p class="notice error" role="alert">{message}</p>{/if}
  <div class="primary-actions"><button type="button" class="primary" data-track="m2-submit" disabled={busy || !tags.length || pendingTag} onclick={submit}>Выполнить</button></div>
  {#if result}<ResultsTable {result} />{/if}
</div>

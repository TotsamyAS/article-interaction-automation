<script lang="ts">
  import { api, workbenchError } from '../api';
  import { submitTrialAttempt } from '../trial-actions';
  import type { AttemptView, ManualQueryHelp, Query, QueryResult, TrialView } from '../types';
  import type { TrialEventLogger } from '../event-logger';
  import TagInput from './TagInput.svelte';
  import QueryInspector from './QueryInspector.svelte';
  import ResultsTable from './ResultsTable.svelte';
  import WorkbenchGuide from './WorkbenchGuide.svelte';

  let { trial, help, logger, onAttempt, onBusy, validationTaskId }: {
    trial: TrialView; help: ManualQueryHelp; logger: TrialEventLogger;
    onAttempt: (attempt: AttemptView) => void; onBusy: (busy: boolean, text?: string) => void;
    validationTaskId?: string;
  } = $props();

  let tags = $state<string[]>([]);
  let pendingTag = $state(false);
  let result = $state<QueryResult | null>(null);
  let previewQuery = $state<Query | null>(null);
  let previewTags = $state('');
  let message = $state('');
  let busy = $state(false);
  let retryId = $state<string | null>(null);
  let retryQuery = $state('');

  const currentTags = $derived(JSON.stringify(tags));
  const previewCurrent = $derived(Boolean(previewQuery) && !pendingTag && previewTags === currentTags);

  async function preview() {
    message = '';
    if (!tags.length) { message = 'Добавьте хотя бы одно условие. Например, обычный фильтр или операцию группировки.'; return; }
    if (pendingTag) { message = 'Завершите текущее условие: выберите значение и подтвердите его Enter/Tab либо отмените ввод.'; return; }
    busy = true; onBusy(true, 'Компилируем условия и обновляем предпросмотр…');
    try {
      const query = await logger.measure('m2-compile', () => api<Query>('/api/manual-query/compile', {
        method: 'POST', body: JSON.stringify({ tags })
      }));
      const base = validationTaskId ? `/api/validation/tasks/${validationTaskId}` : `/api/trials/${trial.id}`;
      const next = await logger.measure('m2-preview', () => api<QueryResult>(`${base}/preview`, {
        method: 'POST', body: JSON.stringify(query)
      }));
      await logger.flushSafely();
      previewQuery = query; previewTags = JSON.stringify(tags); result = next;
    } catch (error) { message = workbenchError(error); }
    finally { busy = false; onBusy(false); }
  }

  async function submit() {
    message = '';
    if (!previewQuery || !previewCurrent) { message = 'Сначала обновите предпросмотр после последних изменений. Итоговая попытка должна засчитать именно показанный Query.'; return; }
    busy = true; onBusy(true, 'Отправляем показанный результат как итоговый ответ…');
    try {
      const serialized = JSON.stringify(previewQuery);
      if (!retryId || retryQuery !== serialized) { retryId = crypto.randomUUID(); retryQuery = serialized; }
      const attempt = await submitTrialAttempt(trial, previewQuery, logger, retryId, validationTaskId);
      retryId = null; retryQuery = '';
      result = attempt.result;
      onAttempt(attempt);
    } catch (error) { message = workbenchError(error); }
    finally { busy = false; onBusy(false); }
  }
</script>

<div class="workbench" data-track="m2-workbench">
  <div class="workbench-heading"><div><span class="mode-pill">M2 · Form</span><h2>Конструктор запроса</h2></div></div>
  <p class="instruction">{help.instruction}</p>
  <WorkbenchGuide mode="M2" />
  <section class="control-card">
    <div class="section-heading"><h3>1. Соберите условия</h3></div>
    <p class="muted small">Каждое готовое условие становится отдельным блоком. Обычные фильтры ограничивают строки; «Группировка», «Итог» и «Экстремум» управляют расчётом над ними.</p>
    <TagInput bind:value={tags} bind:pending={pendingTag} builders={help.builders} placeholder={help.placeholder} disabled={busy} maxTags={40} />
    <div class="syntax-hints"><span>Примеры:</span>{#each help.examples as example}<code>{example}</code>{/each}</div>
  </section>
  <section class="control-card compact-help-card">
    <h3>2. Если нужно найти самую большую/маленькую группу</h3>
    <p><strong>Группировка</strong> выбирает, по чему разделить строки → <strong>Итог</strong> задаёт число для каждой группы → <strong>Экстремум</strong> выбирает максимум или минимум. Например, «больше всего задач у исполнителя» означает группировку по исполнителю + итог «Количество» + экстремум «Максимум».</p>
    <p class="muted small">«Сортировка» только меняет порядок строк в нижней таблице. Она не выбирает группу-победителя.</p>
  </section>

  {#if message}<p class="notice error" role="alert"><strong>Не удалось выполнить действие.</strong> {message}</p>{/if}
  {#if previewQuery && !previewCurrent}<p class="notice warning">Условия изменены после последнего предпросмотра. Таблица ниже устарела — обновите её перед итоговой отправкой.</p>{/if}
  <div class="preview-actions">
    <button type="button" class="secondary" data-track="m2-preview" disabled={busy || !tags.length || pendingTag} onclick={preview}>{result ? 'Обновить предпросмотр' : 'Показать предпросмотр'}</button>
    <button type="button" class="primary" data-track="m2-submit" disabled={busy || !previewCurrent} onclick={submit}>Отправить итоговый ответ</button>
  </div>
  {#if previewQuery}<QueryInspector query={previewQuery} />{/if}
  {#if result}<ResultsTable {result} query={previewQuery} />{:else}<p class="preview-placeholder">После предпросмотра здесь появятся группы, их значения и строки результата.</p>{/if}
</div>

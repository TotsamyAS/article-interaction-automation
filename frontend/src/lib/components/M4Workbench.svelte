<script lang="ts">
  import { downloadUrl } from '../access-context';
  import { ApiError, errorMessage, workbenchError } from '../api';
  import { previewTextRequest, submitM4Attempt } from '../trial-actions';
  import type { AttemptView, Query, QueryResult, TrialView } from '../types';
  import type { TrialEventLogger } from '../event-logger';
  import QueryInspector from './QueryInspector.svelte';
  import ResultsTable from './ResultsTable.svelte';

  let { trial, logger, onAttempt, onBusy }: {
    trial: TrialView; logger: TrialEventLogger;
    onAttempt: (attempt: AttemptView) => void; onBusy: (busy: boolean, text?: string) => void;
  } = $props();

  let transcript = $state('');
  let result = $state<QueryResult | null>(null);
  let previewQuery = $state<Query | null>(null);
  let previewText = $state('');
  let requestId = $state<string | null>(null);
  let requestText = $state('');
  let message = $state('');
  let listening = $state(false);
  let busy = $state(false);
  let recognition: any = null;

  const firefox = typeof navigator !== 'undefined' && /Firefox\//.test(navigator.userAgent);
  const speechCtor = typeof window !== 'undefined' ? ((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition) : undefined;
  const supported = Boolean(speechCtor) && !firefox;
  const previewCurrent = $derived(Boolean(previewQuery && requestId) && transcript.trim() === previewText && requestText === previewText);

  function finishSpeech() {
    if (!listening) return;
    listening = false;
    logger.speechFinished('m4-speech');
  }
  function startSpeech() {
    message = '';
    if (!supported || !speechCtor) {
      message = firefox ? 'Firefox не поддерживает используемый Web Speech API. Откройте эту ссылку в другом современном браузере.' : 'В этом браузере распознавание речи недоступно. Откройте стенд в браузере с Web Speech API.';
      return;
    }
    transcript = '';
    recognition = new speechCtor();
    recognition.lang = 'ru-RU'; recognition.continuous = false; recognition.interimResults = false; recognition.maxAlternatives = 1;
    recognition.onresult = (event: any) => { const value = event?.results?.[0]?.[0]?.transcript; if (typeof value === 'string') transcript = value.trim(); };
    recognition.onerror = (event: any) => { message = event?.error === 'not-allowed' ? 'Нет доступа к микрофону. Разрешите использование микрофона и повторите.' : `Речь не удалось распознать${event?.error ? ` (${event.error})` : ''}. Повторите запись.`; };
    recognition.onend = finishSpeech;
    listening = true; logger.speechStarted('m4-speech');
    try { recognition.start(); } catch (error) { finishSpeech(); message = errorMessage(error); }
  }
  function stopSpeech() { try { recognition?.stop(); } finally { finishSpeech(); } }

  async function preview() {
    const value = transcript.trim(); message = '';
    if (!value) { message = 'Сначала произнесите формулировку задачи и дождитесь распознанного текста.'; return; }
    if (!requestId || requestText !== value) { requestId = crypto.randomUUID(); requestText = value; }
    busy = true; onBusy(true, 'RouterAI интерпретирует распознанную речь для предпросмотра…');
    try {
      const next = await previewTextRequest(trial, value, logger, requestId, 'm4-preview');
      previewQuery = next.query; result = next.result; previewText = value;
    } catch (error) {
      message = workbenchError(error);
      if (error instanceof ApiError) { requestId = null; requestText = ''; }
    } finally { busy = false; onBusy(false); }
  }

  async function submit() {
    message = '';
    if (!previewCurrent || !requestId) { message = 'Сначала получите актуальный предпросмотр распознанной фразы. Итоговая кнопка засчитывает именно его без повторного вызова RouterAI.'; return; }
    busy = true; onBusy(true, 'Фиксируем показанный предпросмотр как итоговый ответ…');
    try {
      const attempt = await submitM4Attempt(trial, previewText, logger, requestId);
      result = attempt.result;
      if (attempt.export_url) { const link = document.createElement('a'); link.href = downloadUrl(attempt.export_url); link.download = 'result.csv'; link.click(); }
      onAttempt(attempt);
    } catch (error) { message = workbenchError(error); }
    finally { busy = false; onBusy(false); }
  }
</script>

<div class="workbench" data-track="m4-workbench">
  <div class="workbench-heading"><div><span class="mode-pill">M4 · Speech</span><h2>Голосовая формулировка</h2></div></div>
  <p class="instruction">Произнесите цель, проверьте распознанный текст и откройте предпросмотр. Web Speech API даёт текст, после чего используется тот же RouterAI-компилятор и общий executor, что в M3.</p>
  {#if firefox}<p class="notice warning" role="alert">Firefox не подходит для M4: откройте эту же персональную ссылку в другом современном браузере.</p>{/if}
  {#if !supported && !firefox}<p class="notice warning" role="alert">Web Speech API в этом браузере недоступен. Для M4 используйте браузер с поддержкой распознавания речи.</p>{/if}
  <div class="primary-actions">
    {#if listening}<button type="button" class="secondary" data-track="m4-stop" onclick={stopSpeech}>Остановить запись</button>
    {:else}<button type="button" class="secondary" data-track="m4-record" disabled={!supported || busy} onclick={startSpeech}>{transcript ? 'Записать заново' : 'Начать запись'}</button>{/if}
  </div>
  <textarea data-track="m4-transcript" rows="4" readonly aria-label="Распознанный текст" placeholder="Здесь появится распознанная речь" value={transcript}></textarea>
  {#if message}<p class="notice error" role="alert"><strong>Не удалось выполнить действие.</strong> {message}</p>{/if}
  {#if previewQuery && !previewCurrent}<p class="notice warning">Распознанный текст изменился после предпросмотра. Обновите предпросмотр перед итоговой отправкой.</p>{/if}
  <div class="preview-actions">
    <button type="button" class="secondary" data-track="m4-preview" disabled={busy || listening || !transcript.trim()} onclick={preview}>{result ? 'Обновить предпросмотр' : 'Показать предпросмотр'}</button>
    <button type="button" class="primary" data-track="m4-submit" disabled={busy || listening || !previewCurrent} onclick={submit}>Отправить итоговый ответ</button>
  </div>
  {#if previewQuery}<QueryInspector query={previewQuery} />{/if}
  {#if result}<ResultsTable {result} query={previewQuery} />{:else}<p class="preview-placeholder">После предпросмотра здесь появится таблица результата и выполненный Query.</p>{/if}
</div>

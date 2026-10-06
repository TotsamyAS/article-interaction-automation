<script lang="ts">
  import { onDestroy } from 'svelte';
  import { api, ApiError, errorMessage, workbenchError } from '../api';
  import { previewTextRequest, reuseTaskPromptAttempt, submitM4Attempt } from '../trial-actions';
  import type { AttemptView, M4TranscriptionView, Query, QueryResult, TrialView } from '../types';
  import type { TrialEventLogger } from '../event-logger';
  import ModeHint from './ModeHint.svelte';
  import QueryInspector from './QueryInspector.svelte';
  import ResultsTable from './ResultsTable.svelte';

  let { trial, logger, onAttempt, onBusy, validationTaskId }: {
    trial: TrialView; logger: TrialEventLogger;
    onAttempt: (attempt: AttemptView) => void; onBusy: (busy: boolean, text?: string) => void;
    validationTaskId?: string;
  } = $props();

  const MAX_RECORDING_MS = 20_000;
  const mediaSupported = typeof navigator !== 'undefined'
    && Boolean(navigator.mediaDevices?.getUserMedia)
    && typeof MediaRecorder !== 'undefined';

  let transcript = $state('');
  let result = $state<QueryResult | null>(null);
  let previewQuery = $state<Query | null>(null);
  let previewText = $state('');
  let requestId = $state<string | null>(null);
  let requestText = $state('');
  let message = $state('');
  let listening = $state(false);
  let busy = $state(false);
  let recorder: MediaRecorder | null = null;
  let mediaStream: MediaStream | null = null;
  let chunks: BlobPart[] = [];
  let recordingStartedAt = 0;
  let recordingTimer: number | null = null;
  let destroyed = false;

  const previewCurrent = $derived(Boolean(previewQuery && requestId) && transcript.trim() === previewText && requestText === previewText);

  function recordingMimeType() {
    const candidates = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4', 'audio/webm'];
    return candidates.find((value) => MediaRecorder.isTypeSupported(value)) ?? '';
  }

  function clearRecordingTimer() {
    if (recordingTimer !== null) window.clearTimeout(recordingTimer);
    recordingTimer = null;
  }

  function stopTracks() {
    mediaStream?.getTracks().forEach((track) => track.stop());
    mediaStream = null;
  }

  function invalidatePreview() {
    transcript = '';
    result = null;
    previewQuery = null;
    previewText = '';
    requestId = null;
    requestText = '';
  }

  function finishSpeechEvent() {
    if (!listening) return;
    listening = false;
    logger.speechFinished('m4-speech');
  }

  async function transcribeRecording(stoppedRecorder: MediaRecorder) {
    finishSpeechEvent();
    clearRecordingTimer();
    stopTracks();
    if (destroyed) return;

    const mimeType = stoppedRecorder.mimeType || 'audio/webm';
    const blob = new Blob(chunks, { type: mimeType });
    chunks = [];
    if (!blob.size) {
      message = 'Браузер не записал звук. Проверьте микрофон и повторите.';
      return;
    }

    const durationMs = Math.max(1, Math.min(MAX_RECORDING_MS, Math.round(performance.now() - recordingStartedAt)));
    const asrRequestId = crypto.randomUUID();
    busy = true;
    onBusy(true, 'RouterAI распознаёт речь…');
    logger.requestStarted(asrRequestId, 'm4-transcribe');
    try {
      const params = new URLSearchParams({ request_id: asrRequestId, duration_ms: String(durationMs) });
      const base = validationTaskId ? `/api/validation/tasks/${validationTaskId}` : `/api/trials/${trial.id}`;
      const recognized = await api<M4TranscriptionView>(`${base}/m4-transcribe?${params}`, {
        method: 'POST',
        headers: { 'Content-Type': mimeType },
        body: blob
      });
      transcript = recognized.text.trim();
      if (!transcript) message = 'Речь не распознана. Повторите запись ближе к микрофону.';
    } catch (error) {
      message = workbenchError(error);
    } finally {
      logger.requestFinished(asrRequestId, 'm4-transcribe');
      await logger.flushSafely();
      busy = false;
      onBusy(false);
    }
  }

  async function startSpeech() {
    message = '';
    if (!mediaSupported) {
      message = 'Этот браузер не умеет записывать звук через MediaRecorder. Используйте актуальный Chrome, Edge или Firefox.';
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        video: false
      });
      mediaStream = stream;
      const mimeType = recordingMimeType();
      const nextRecorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      recorder = nextRecorder;
      chunks = [];
      invalidatePreview();
      nextRecorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      nextRecorder.onerror = () => {
        message = 'Не удалось записать звук. Проверьте доступ к микрофону и повторите.';
        try { if (nextRecorder.state !== 'inactive') nextRecorder.stop(); } catch { /* already stopping */ }
      };
      nextRecorder.onstop = () => { void transcribeRecording(nextRecorder); };
      nextRecorder.start(250);
      recordingStartedAt = performance.now();
      listening = true;
      logger.speechStarted('m4-speech');
      recordingTimer = window.setTimeout(stopSpeech, MAX_RECORDING_MS);
    } catch (error) {
      clearRecordingTimer();
      stopTracks();
      const name = error instanceof DOMException ? error.name : '';
      message = name === 'NotAllowedError'
        ? 'Нет доступа к микрофону. Разрешите использование микрофона для этого сайта и повторите.'
        : `Не удалось открыть микрофон. ${errorMessage(error)}`;
    }
  }

  function stopSpeech() {
    clearRecordingTimer();
    if (!recorder || recorder.state === 'inactive') {
      finishSpeechEvent();
      stopTracks();
      return;
    }
    try { recorder.stop(); }
    catch (error) {
      finishSpeechEvent();
      stopTracks();
      message = errorMessage(error);
    }
  }

  async function preview() {
    const value = transcript.trim(); message = '';
    if (!value) { message = 'Сначала произнесите формулировку задачи и дождитесь серверного распознавания.'; return; }
    if (!requestId || requestText !== value) { requestId = crypto.randomUUID(); requestText = value; }
    busy = true; onBusy(true, 'RouterAI интерпретирует распознанную речь для предпросмотра…');
    try {
      const next = await previewTextRequest(trial, value, logger, requestId, 'm4-preview', validationTaskId);
      previewQuery = next.query; result = next.result; previewText = value;
    } catch (error) {
      message = workbenchError(error);
      if (error instanceof ApiError) { requestId = null; requestText = ''; }
    } finally { busy = false; onBusy(false); }
  }

  async function reuseCurrentPrompt() {
    message = '';
    busy = true; onBusy(true, 'Переиспользуем текст задания как запрос и сразу засчитываем попытку…');
    try {
      const attempt = await reuseTaskPromptAttempt(trial, logger, 'M4', validationTaskId);
      result = attempt.result;
      previewQuery = null;
      onAttempt(attempt);
    } catch (error) { message = workbenchError(error); }
    finally { busy = false; onBusy(false); }
  }

  async function submit() {
    message = '';
    if (!previewCurrent || !requestId) { message = 'Сначала получите актуальный предпросмотр распознанной фразы. Итоговая кнопка засчитывает именно его без повторного вызова RouterAI.'; return; }
    busy = true; onBusy(true, 'Фиксируем показанный предпросмотр как итоговый ответ…');
    try {
      const attempt = await submitM4Attempt(trial, previewText, logger, requestId, validationTaskId);
      result = attempt.result;
      onAttempt(attempt);
    } catch (error) { message = workbenchError(error); }
    finally { busy = false; onBusy(false); }
  }

  onDestroy(() => {
    destroyed = true;
    clearRecordingTimer();
    if (recorder && recorder.state !== 'inactive') {
      recorder.onstop = null;
      try { recorder.stop(); } catch { /* component is already leaving */ }
    }
    finishSpeechEvent();
    stopTracks();
  });
</script>

<div class="workbench" data-track="m4-workbench">
  <div class="workbench-heading"><div><span class="mode-pill">M4 · Speech</span><h2>Голосовая формулировка</h2></div></div>
  <ModeHint {logger} track="m4-mode-hint">
    <p class="instruction">Произнесите цель, остановите запись и дождитесь расшифровки. Аудио отправляется сервером стенда в RouterAI для распознавания речи; VPN и Web Speech API в браузере не используются. Проверьте распознанный текст, затем откройте предпросмотр RouterAI.</p>
  </ModeHint>
  {#if !mediaSupported}<p class="notice warning" role="alert">В этом браузере нет MediaRecorder или доступа к микрофону. Для M4 используйте актуальный Chrome, Edge или Firefox.</p>{/if}
  <div class="primary-actions">
    {#if listening}<button type="button" class="secondary" data-track="m4-stop" onclick={stopSpeech}>Остановить запись</button>
    {:else}<button type="button" class="secondary" data-track="m4-record" disabled={!mediaSupported || busy} onclick={startSpeech}>{transcript ? 'Записать заново' : 'Начать запись'}</button>{/if}
  </div>
  <p class="muted small">Максимальная длительность одной записи — 20 секунд. Аудиофайл после распознавания не сохраняется.</p>
  <textarea data-track="m4-transcript" rows="4" readonly aria-label="Распознанный текст" placeholder="Здесь появится расшифровка RouterAI" value={transcript}></textarea>
  {#if message}<p class="notice error" role="alert"><strong>Не удалось выполнить действие.</strong> {message}</p>{/if}
  {#if previewQuery && !previewCurrent}<p class="notice warning">Распознанный текст изменился после предпросмотра. Обновите предпросмотр перед итоговой отправкой.</p>{/if}
  <div class="preview-actions reuse-prompt-actions">
    <button type="button" class="secondary" data-track="m4-reuse-prompt" disabled={busy} onclick={reuseCurrentPrompt}>Переиспользовать текущий промпт</button>
    <span class="muted small">Сразу засчитывается как попытка. Повтор того же текста не увеличивает счётчик разных попыток.</span>
  </div>
  <div class="preview-actions">
    <button type="button" class="secondary" data-track="m4-preview" disabled={busy || listening || !transcript.trim()} onclick={preview}>{result ? 'Обновить предпросмотр' : 'Показать предпросмотр'}</button>
    <button type="button" class="primary" data-track="m4-submit" disabled={busy || listening || !previewCurrent} onclick={submit}>Отправить итоговый ответ</button>
  </div>
  {#if previewQuery}<QueryInspector query={previewQuery} />{/if}
  {#if result}<ResultsTable {result} query={previewQuery} />{:else}<p class="preview-placeholder">После предпросмотра здесь появится таблица результата и выполненный Query.</p>{/if}
</div>

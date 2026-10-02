<script lang="ts">
  import { onMount } from 'svelte';
  import { api, ApiError, errorMessage } from './lib/api';
  import { TrialEventLogger } from './lib/event-logger';
  import type { AttemptView, ManualQueryHelp, MeResponse, SessionView, TaskRecord, TrialView } from './lib/types';
  import AppToast from './lib/components/AppToast.svelte';
  import ConfirmDialog from './lib/components/ConfirmDialog.svelte';
  import LiveSearchProgress from './lib/components/LiveSearchProgress.svelte';
  import M1Workbench from './lib/components/M1Workbench.svelte';
  import M2Workbench from './lib/components/M2Workbench.svelte';
  import M3Workbench from './lib/components/M3Workbench.svelte';
  import M4Workbench from './lib/components/M4Workbench.svelte';
  import M5Workbench from './lib/components/M5Workbench.svelte';
  import HelpDrawer from './lib/components/HelpDrawer.svelte';
  import IntroDeck from './lib/components/IntroDeck.svelte';
  import AdminValidation from './lib/components/AdminValidation.svelte';
  import AnimatedTaskPrompt from './lib/components/AnimatedTaskPrompt.svelte';
  import { downloadUrl, initializeAccessContext, setAccessContext } from './lib/access-context';

  let authState = $state<'loading' | 'authorized' | 'unauthorized' | 'error'>('loading');
  let me = $state<MeResponse | null>(null);
  let records = $state<TaskRecord[]>([]);
  let manualHelp = $state<ManualQueryHelp | null>(null);
  let selectedKind = $state<'experiment' | 'practice'>('experiment');
  let requestBusy = $state(false);
  let busyText = $state('');
  let fatalMessage = $state('');
  let toast = $state<{ tone: 'success' | 'error'; text: string; key: number } | null>(null);
  let logoutOpen = $state(false);
  let now = $state(performance.now());
  let logger = $state<TrialEventLogger | null>(null);
  let loggerTrialId = $state('');
  let expiredRefreshId = $state('');
  let eventWarning = $state('');
  let helpOpen = $state(false);
  let protocol = $state<SessionView['manifest'] | null>(null);
  let tabBlocked = $state(false);
  let introOpen = $state(false);
  let introSeen = $state(false);

  const currentSession = $derived(me?.sessions.find((session) => session.kind === selectedKind) ?? null);
  const currentTrial = $derived(currentSession?.trials.find((trial) => trial.status === 'active' || trial.status === 'pending') ?? null);
  const activeTrialId = $derived(currentTrial?.status === 'active' ? currentTrial.id : null);
  const completedTrials = $derived(currentSession?.trials.filter((trial) => trial.status === 'correct' || trial.status === 'incomplete').length ?? 0);
  const progressPercent = $derived(currentSession?.trials.length ? Math.round(completedTrials / currentSession.trials.length * 100) : 0);
  const remainingSeconds = $derived.by(() => {
    void now;
    return currentTrial?.status === 'active' && logger ? Math.ceil(logger.remainingMilliseconds() / 1000) : null;
  });
  const attemptsLeft = $derived(currentTrial ? Math.max(0, currentTrial.attempt_limit - currentTrial.attempts.length) : 0);
  const giveUpRemaining = $derived(currentTrial ? Math.max(0, 3 - currentTrial.unique_attempts) : 3);
  const validationMode = $derived(Boolean(me?.validation_mode));

  function setToast(tone: 'success' | 'error', text: string) {
    toast = { tone, text, key: Date.now() };
  }

  async function load() {
    authState = 'loading'; fatalMessage = '';
    try {
      me = await api<MeResponse>('/api/me');
      setAccessContext(me.access_context);
      authState = 'authorized';
      if (me.role === 'participant') {
        const [loadedRecords, loadedHelp, loadedProtocol] = await Promise.all([
          api<TaskRecord[]>('/api/records'),
          api<ManualQueryHelp>('/api/manual-query'),
          api<{ manifest: SessionView['manifest'] }>('/api/protocol')
        ]);
        records = loadedRecords; manualHelp = loadedHelp; protocol = loadedProtocol.manifest;
        if (!me.validation_mode) {
          const key = `intro-complete:${me.access_context}`;
          introSeen = localStorage.getItem(key) === '1';
          introOpen = !introSeen;
        }
        if (!me.sessions.some((session) => session.kind === selectedKind) && me.sessions.some((session) => session.kind === 'practice')) selectedKind = 'practice';
        const completedExperiment = me.sessions.find((session) => session.kind === 'experiment' && session.complete && session.completion_code);
        if (completedExperiment) downloadCompletionCode(completedExperiment, true);
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) authState = 'unauthorized';
      else { authState = 'error'; fatalMessage = errorMessage(error); }
    }
  }

  async function createSession(kind: 'experiment' | 'practice') {
    if (!me || requestBusy) return;
    requestBusy = true; busyText = kind === 'experiment' ? 'Создаём основную сессию…' : 'Создаём тренировку…';
    try {
      const session = await api<SessionView>('/api/sessions', { method: 'POST', body: JSON.stringify({ participant_code: me.participant_code, kind }) });
      me.sessions = [...me.sessions.filter((item) => item.kind !== kind), session];
      selectedKind = kind;
    } catch (error) { setToast('error', errorMessage(error)); }
    finally { requestBusy = false; busyText = ''; }
  }

  async function refreshSession(sessionId = currentSession?.id) {
    if (!me || !sessionId) return null;
    try {
      const session = await api<SessionView>(`/api/sessions/${sessionId}`);
      me.sessions = me.sessions.map((item) => item.id === session.id ? session : item);
      if (session.complete && session.kind === 'experiment') downloadCompletionCode(session, true);
      return session;
    } catch (error) { setToast('error', errorMessage(error)); return null; }
  }

  function downloadCompletionCode(session: SessionView, once = false) {
    if (session.kind !== 'experiment' || !session.complete || !session.completion_code) return;
    const marker = `completion-code-downloaded:${session.id}:${session.completion_code}`;
    if (once && localStorage.getItem(marker) === '1') return;
    const link = document.createElement('a');
    link.href = downloadUrl(`/api/sessions/${session.id}/completion-code.txt`);
    link.download = 'completion-code.txt';
    link.click();
    if (once) localStorage.setItem(marker, '1');
  }

  async function startTrial(trial: TrialView) {
    requestBusy = true; busyText = 'Открываем пробу…';
    try {
      const started = await api<TrialView>(`/api/trials/${trial.id}/start`, { method: 'POST' });
      if (currentSession) {
        const updated = { ...currentSession, trials: currentSession.trials.map((item) => item.id === started.id ? started : item) };
        if (me) me.sessions = me.sessions.map((item) => item.id === updated.id ? updated : item);
      }
      logger?.navigation(`trial:${started.id}:start`);
    } catch (error) { setToast('error', errorMessage(error)); }
    finally { requestBusy = false; busyText = ''; }
  }

  async function giveUp() {
    if (!currentTrial || !currentTrial.give_up_available || requestBusy) return;
    requestBusy = true; busyText = 'Завершаем пробу без ответа…';
    try {
      const surrendered = await api<TrialView>(`/api/trials/${currentTrial.id}/give-up`, { method: 'POST' });
      if (currentSession && me) {
        const updated = { ...currentSession, trials: currentSession.trials.map((item) => item.id === surrendered.id ? surrendered : item) };
        me.sessions = me.sessions.map((item) => item.id === updated.id ? updated : item);
      }
      setToast('success', 'Проба завершена: участник сдался после трёх разных попыток.');
      await refreshSession(surrendered.session_id);
    } catch (error) { setToast('error', errorMessage(error)); }
    finally { requestBusy = false; busyText = ''; }
  }

  async function handleAttempt(attempt: AttemptView) {
    if (attempt.correct) setToast('success', 'Результат верный. Проба завершена.');
    else if (attempt.trial_status === 'incomplete') setToast('error', 'Лимит попыток исчерпан. Проба завершена.');
    else setToast('error', 'Результат не совпал с эталоном. Исправьте запрос и повторите.');
    await refreshSession(attempt.trial_id === currentTrial?.id ? currentSession?.id : undefined);
  }

  function handleBusy(value: boolean, text = '') { requestBusy = value; busyText = text; }

  async function logout() {
    logoutOpen = false;
    try { await api('/api/access/logout', { method: 'POST' }); }
    finally { location.assign('/'); }
  }

  function completeIntro() {
    if (!me) return;
    localStorage.setItem(`intro-complete:${me.access_context}`, '1');
    introSeen = true;
    introOpen = false;
  }

  function formatTime(seconds: number | null) {
    if (seconds === null) return '—';
    const minutes = Math.floor(seconds / 60);
    return `${minutes}:${String(seconds % 60).padStart(2, '0')}`;
  }

  onMount(() => {
    initializeAccessContext();
    void load();
    const timer = window.setInterval(() => now = performance.now(), 1000);
    return () => window.clearInterval(timer);
  });

  $effect(() => {
    const id = activeTrialId;
    if (!id) { tabBlocked = false; return; }
    let cancelled = false;
    let release: (() => void) | undefined;
    let ownedLogger: TrialEventLogger | null = null;
    tabBlocked = false;
    const run = async () => {
      const latest = await api<TrialView>(`/api/trials/${id}`);
      if (cancelled) return;
      if (latest.status !== 'active') { await refreshSession(latest.session_id); return; }
      eventWarning = '';
      ownedLogger = new TrialEventLogger(latest, (message) => eventWarning = message,
        () => performance.now(), () => { void refreshSession(latest.session_id); });
      logger = ownedLogger; loggerTrialId = id;
      ownedLogger.start();
      await new Promise<void>((resolve) => release = resolve);
      await ownedLogger.stop();
    };
    // Locks are released by the browser on tab close. No credential is stored here.
    if (navigator.locks) {
      void navigator.locks.request(`experiment-trial:${id}`, { ifAvailable: true }, async (lock) => {
        if (!lock) { if (!cancelled) tabBlocked = true; return; }
        await run();
      }).catch((error) => { if (!cancelled) eventWarning = errorMessage(error); });
    } else {
      tabBlocked = true;
      eventWarning = 'Для защиты журнала от нескольких вкладок откройте стенд в современном браузере через HTTPS или localhost.';
    }
    return () => {
      cancelled = true; release?.();
      logger = null; loggerTrialId = '';
    };
  });

  $effect(() => {
    const trial = currentTrial;
    if (trial?.status === 'active' && remainingSeconds === 0 && expiredRefreshId !== trial.id) {
      expiredRefreshId = trial.id;
      void refreshSession();
    }
  });
</script>

<svelte:head><meta name="robots" content="noindex,nofollow" /></svelte:head>

{#if authState === 'loading'}
  <main class="center-page"><div class="loading-card"><span class="progress-spinner large" aria-hidden="true"></span><h1>Экспериментальный стенд</h1><p>Проверяем доступ…</p></div></main>
{:else if authState === 'unauthorized'}
  <main class="center-page">
    <section class="access-card"><span class="brand-mark">HM</span><p class="eyebrow">Экспериментальный стенд</p><h1>Нужна персональная ссылка</h1>
      <p>Откройте индивидуальную ссылку приглашения, выданную исследователем. Она восстановит ваш код участника и сохранённый прогресс.</p>
      <p class="muted">Формы регистрации здесь нет: код участника привязан к приглашению.</p></section>
  </main>
{:else if authState === 'error'}
  <main class="center-page"><section class="access-card"><h1>Стенд недоступен</h1><p class="notice error">{fatalMessage}</p><button class="primary" onclick={load}>Повторить</button></section></main>
{:else if me?.role === 'researcher'}
  <main class="shell researcher-shell">
    <header class="topbar"><div><p class="eyebrow">Экспериментальный стенд</p><h1>Выгрузка исследования</h1></div><button class="secondary" onclick={() => logoutOpen = true}>Выйти</button></header>
    <section class="research-card"><h2>Данные для анализа</h2><p>Отдельная аналитическая панель не используется. Скачайте полный Excel или CSV-архив и продолжайте анализ в табличном ПО.</p>
      <div class="download-actions"><a class="primary button-link" href={downloadUrl('/api/analytics/export.xlsx')}>Скачать Excel</a><a class="secondary button-link" href={downloadUrl('/api/analytics/export.zip')}>Скачать CSV ZIP</a></div>
      <p class="muted small">Роль: researcher · код: {me.participant_code}</p></section>
  </main>
{:else if me?.validation_mode && manualHelp && protocol}
  <AdminValidation participantCode={me.participant_code} {records} {manualHelp} referenceDate={protocol.protocol.reference_date} onLogout={() => { logoutOpen = true; }} />
{:else if me}
  <main class="shell">
    <header class="topbar">
      <div><p class="eyebrow">Экспериментальный стенд</p><h1>Рабочая сессия</h1></div>
      <div class="participant-meta"><span class="code-badge">{me.participant_code}</span><button class="secondary" onclick={() => introOpen = true}>Инструктаж</button><button class="secondary" data-track="help-open" onclick={() => helpOpen = true}>Как работать</button><button class="secondary" onclick={() => logoutOpen = true}>Выйти</button></div>
    </header>

    <nav class="session-tabs" aria-label="Тип сессии">
      <button class:active={selectedKind === 'experiment'} onclick={() => selectedKind = 'experiment'}>Основная сессия</button>
      <button class:active={selectedKind === 'practice'} onclick={() => selectedKind = 'practice'}>Тренировка</button>
    </nav>

    {#if eventWarning}<p class="notice warning" role="alert">{eventWarning}</p>{/if}
    {#if tabBlocked}<p class="notice warning" role="alert">Эта проба уже открыта в другой вкладке либо блокировка вкладок недоступна. Продолжайте в первой вкладке или закройте её и <button class="secondary compact" onclick={() => location.reload()}>Обновите эту</button>.</p>{/if}

    {#if !currentSession}
      <section class="start-card"><p class="eyebrow">{selectedKind === 'experiment' ? '15 проб' : 'Тренировочный режим'}</p>
        <h2>{selectedKind === 'experiment' ? 'Основная сессия ещё не начата' : 'Тренировка ещё не начата'}</h2>
        <p>{selectedKind === 'experiment' ? 'После старта порядок режимов и варианты задач фиксируются для вашего кода.' : 'Тренировочные результаты не входят в основной анализ.'}</p>
        {#if !introSeen}<p class="notice warning">Перед первой сессией завершите короткий визуальный инструктаж.</p><button class="primary" onclick={() => introOpen = true}>Открыть инструктаж</button>
        {:else}<button class="primary" disabled={requestBusy} onclick={() => createSession(selectedKind)}>{selectedKind === 'experiment' ? 'Создать сессию' : 'Начать тренировку'}</button>{/if}
      </section>
    {:else}
      <LiveSearchProgress loading={requestBusy} statusText={requestBusy ? busyText : currentSession.complete ? 'Сессия завершена' : 'Прогресс сессии'}
        progress={progressPercent} detail={`Завершено ${completedTrials} из ${currentSession.trials.length} проб`} />

      {#if currentSession.complete}
        <section class="complete-card"><span class="success-icon">✓</span><h2>Сессия завершена</h2><p>Все доступные пробы сохранены.</p>
          {#if currentSession.kind === 'experiment' && currentSession.completion_code}
            <p>Ваш проверочный код прохождения:</p>
            <p class="completion-code"><strong>{currentSession.completion_code}</strong></p>
            <p class="muted small">Код также записан в реестре исследователя. Сохраните TXT-файл: по этому коду можно сверить присланный результат с записью на сервере.</p>
            <button class="primary" onclick={() => downloadCompletionCode(currentSession)}>Скачать проверочный код (.txt)</button>
          {/if}
        </section>
      {:else if currentTrial}
        <section class="trial-card">
          <div class="trial-heading">
            <div><span class="mode-pill">{currentTrial.mode}</span><span class="level-pill">C{currentTrial.level} · TCI {currentTrial.tci}</span></div>
            <div class="trial-stats"><span>Попыток осталось: <strong>{attemptsLeft}</strong></span><span>Время: <strong class:urgent={remainingSeconds !== null && remainingSeconds <= 30}>{formatTime(remainingSeconds)}</strong></span></div>
          </div>

          {#if currentTrial.status === 'pending'}
            {#if currentTrial.mode_available}
              <div class="prompt-placeholder"><h2>Следующая проба готова</h2><p>Формулировка появится после запуска; с этого момента начнётся отсчёт времени.</p>
                <button class="primary" data-track="trial-start" disabled={requestBusy} onclick={() => startTrial(currentTrial)}>Начать пробу</button></div>
            {:else}
              <div class="unavailable-card"><h2>Режим {currentTrial.mode} пока не подключён</h2><p>Backend не подменяет M4–M5 другими интерфейсами. Продолжение этой сессии станет доступно после подключения соответствующего режима.</p></div>
            {/if}
          {:else}
            <div class="task-prompt"><p class="eyebrow">Задание {currentTrial.task_id}</p><AnimatedTaskPrompt text={currentTrial.prompt} /></div>
            {#key currentTrial.id}
            {#if currentTrial.mode === 'M1' && logger && loggerTrialId === currentTrial.id}
              <M1Workbench trial={currentTrial} {records} referenceDate={currentSession.manifest.protocol.reference_date} {logger} onAttempt={handleAttempt} onBusy={handleBusy} />
            {:else if currentTrial.mode === 'M2' && logger && loggerTrialId === currentTrial.id && manualHelp}
              <M2Workbench trial={currentTrial} help={manualHelp} {logger} onAttempt={handleAttempt} onBusy={handleBusy} />
            {:else if currentTrial.mode === 'M3' && logger && loggerTrialId === currentTrial.id}
              <M3Workbench trial={currentTrial} {logger} onAttempt={handleAttempt} onBusy={handleBusy} />
            {:else if currentTrial.mode === 'M4' && logger && loggerTrialId === currentTrial.id}
              <M4Workbench trial={currentTrial} {logger} onAttempt={handleAttempt} onBusy={handleBusy} />
            {:else if currentTrial.mode === 'M5' && logger && loggerTrialId === currentTrial.id}
              <M5Workbench trial={currentTrial} {logger} onAttempt={handleAttempt} onBusy={handleBusy} />
            {/if}
            {/key}
            <div class="give-up-panel">
              <p class="give-up-countdown" aria-live="polite">
                {#if currentTrial.give_up_available}
                  До «Сдаться»: <strong>0 — доступно</strong>
                {:else}
                  До «Сдаться»: <strong>{giveUpRemaining}</strong> разных попыток
                {/if}
              </p>
              <button type="button" class="secondary" data-track="trial-give-up" disabled={requestBusy || !currentTrial.give_up_available} onclick={giveUp}>Сдаться</button>
              <p class="muted small">Разблокируется после 3 попыток с разными входами. Повтор одного и того же запроса не уменьшает счётчик.</p>
            </div>
          {/if}
        </section>
      {/if}
    {/if}
  </main>
{/if}

{#if toast}<AppToast tone={toast.tone} text={toast.text} resetKey={toast.key} onDismiss={() => toast = null} />{/if}
{#if helpOpen && protocol}
  <HelpDrawer attemptLimit={currentTrial?.attempt_limit ?? protocol.protocol.attempt_limit}
    timeLimitSeconds={currentTrial?.trial_limit_seconds ?? protocol.protocol.trial_limit_seconds}
    referenceDate={protocol.protocol.reference_date} activeTrial={currentTrial?.status === 'active'}
    onclose={() => helpOpen = false} />
{/if}
{#if logoutOpen}<ConfirmDialog title="Выйти из стенда?" message="Повторно войти можно по той же персональной ссылке приглашения." confirmLabel="Выйти" onconfirm={logout} oncancel={() => logoutOpen = false} />{/if}

{#if introOpen && me && !validationMode}<IntroDeck oncomplete={completeIntro} onclose={() => introOpen = false} />{/if}


<style>
  .completion-code { margin: 10px auto 16px; font: 800 1.7rem/1 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; letter-spacing: .16em; }
  .give-up-panel { margin-top: 18px; padding-top: 16px; border-top: 1px solid #e5e9f0; display: grid; justify-items: start; gap: 8px; }
  .give-up-countdown { margin: 0; font-size: .9rem; color: #55627a; }
  .give-up-countdown strong { color: #172033; font-size: 1.05rem; }
</style>

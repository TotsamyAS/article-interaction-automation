<script lang="ts">
  import { onMount } from 'svelte';
  import { api, errorMessage } from '../api';
  import { TrialEventLogger } from '../event-logger';
  import type { AttemptView, ManualQueryHelp, Mode, TaskRecord, TrialView, ValidationTask } from '../types';
  import M1Workbench from './M1Workbench.svelte';
  import M2Workbench from './M2Workbench.svelte';
  import M3Workbench from './M3Workbench.svelte';
  import M4Workbench from './M4Workbench.svelte';
  import M5Workbench from './M5Workbench.svelte';
  import QueryInspector from './QueryInspector.svelte';
  import ResultsTable from './ResultsTable.svelte';
  import IntroDeck from './IntroDeck.svelte';

  let { participantCode, records, manualHelp, referenceDate, onLogout }: {
    participantCode: string;
    records: TaskRecord[];
    manualHelp: ManualQueryHelp;
    referenceDate: string;
    onLogout: () => void;
  } = $props();

  const modes: Mode[] = ['M1', 'M2', 'M3', 'M4', 'M5'];
  let tasks = $state<ValidationTask[]>([]);
  let selectedId = $state('');
  let selectedMode = $state<Mode>('M1');
  let level = $state<'all' | '1' | '2' | '3'>('all');
  let search = $state('');
  let reveal = $state(false);
  let loading = $state(true);
  let message = $state('');
  let outcome = $state('');
  let busyText = $state('');
  let introOpen = $state(false);

  const visible = $derived(tasks.filter((task) => {
    if (task.training) return false;
    if (level !== 'all' && task.level !== Number(level)) return false;
    const q = search.trim().toLowerCase();
    return !q || task.id.toLowerCase().includes(q) || task.prompt.toLowerCase().includes(q);
  }));
  const selected = $derived(tasks.find((task) => task.id === selectedId) ?? visible[0] ?? null);

  function makeTrial(task: ValidationTask, mode: Mode): TrialView {
    return {
      wording_version: 'admin-validation',
      trial_limit_seconds: 3600,
      attempt_limit: 999,
      id: `validation:${task.id}:${mode}`,
      session_id: 'validation',
      position: 0,
      block_index: 0,
      mode,
      task_id: task.id,
      status: 'active',
      started_ms: 0,
      ended_ms: null,
      end_reason: null,
      prompt: task.prompt,
      tci: task.tci,
      level: task.level,
      mode_available: true,
      deadline_ms: null,
      attempts: [],
      unique_attempts: 0,
      give_up_available: false,
      gave_up: false,
      next_event_sequence: 0,
      last_event_offset_ms: 0,
      elapsed_since_start_ms: 0,
      metrics: {
        actual: { elapsed_ms: 0, Tcorrect_ms: null, Tfirst_ms: null, Tuser_active_ms: 0, A1: null, attempts: 0, Nretry: 0 },
        analysis: { Tcorrect_ms: null, A1: null, Nretry: 0 },
        incomplete: false,
        end_reason: null
      }
    };
  }

  function choose(id: string) {
    selectedId = id;
    reveal = false;
    outcome = '';
  }

  function chooseMode(mode: Mode) {
    selectedMode = mode;
    reveal = false;
    outcome = '';
  }

  function handleAttempt(attempt: AttemptView) {
    outcome = attempt.correct
      ? 'Эталон совпал: такая формулировка/настройка даёт правильный результат.'
      : 'Результат не совпал с эталоном. Форму можно продолжать редактировать и проверять без ограничений.';
  }

  onMount(async () => {
    try {
      tasks = await api<ValidationTask[]>('/api/validation/tasks');
      selectedId = tasks.find((task) => !task.training)?.id ?? '';
    } catch (error) {
      message = errorMessage(error);
    } finally {
      loading = false;
    }
  });
</script>

<main class="shell validation-shell">
  <header class="topbar">
    <div><p class="eyebrow">Режим проверки формулировок</p><h1>Каталог задач · M1–M5</h1></div>
    <div class="participant-meta"><span class="code-badge">{participantCode}</span><button class="secondary" onclick={() => introOpen = true}>Инструктаж</button><button class="secondary" onclick={onLogout}>Выйти</button></div>
  </header>

  <section class="validation-banner">
    <div><strong>Admin validation mode</strong><p>Ниже используются те же компоненты M1–M5, поля, подписи и RouterAI-пайплайны, что у испытуемого. Разница только одна: здесь не создаются экспериментальные сессии/попытки и не записывается журнал действий.</p></div>
    <span class="validation-zero">0 записей журнала</span>
  </section>

  {#if loading}
    <section class="start-card"><p>Загружаем каталог…</p></section>
  {:else if message}
    <section class="start-card"><p class="notice error">{message}</p></section>
  {:else}
    <div class="validation-layout">
      <aside class="validation-sidebar">
        <div class="validation-filters">
          <input aria-label="Поиск задания" placeholder="Поиск по тексту или ID" bind:value={search} />
          <div class="level-tabs">
            {#each ['all', '1', '2', '3'] as item}
              <button class:active={level === item} onclick={() => level = item as typeof level}>{item === 'all' ? 'Все' : `C${item}`}</button>
            {/each}
          </div>
          <p class="muted small">Задач в списке: <strong>{visible.length}</strong></p>
        </div>
        <div class="validation-task-list">
          {#each visible as task (task.id)}
            <button class:selected={selected?.id === task.id} onclick={() => choose(task.id)}>
              <span><strong>{task.id}</strong> · C{task.level} · TCI {task.tci}</span>
              <small>{task.prompt}</small>
            </button>
          {/each}
        </div>
      </aside>

      {#if selected}
        <section class="validation-workspace">
          <div class="task-prompt"><p class="eyebrow">Задание {selected.id} · C{selected.level} · TCI {selected.tci}</p><h2>{selected.prompt}</h2></div>

          <nav class="admin-mode-tabs" aria-label="Режим интерфейса">
            {#each modes as mode}
              <button class:active={selectedMode === mode} onclick={() => chooseMode(mode)}>{mode}</button>
            {/each}
          </nav>
          <p class="muted small admin-mode-note">Переключение режима сбрасывает только локальный черновик. Ничего из этого экрана не входит в данные испытуемых.</p>
          {#if busyText}<p class="notice warning">{busyText}</p>{/if}
          {#if outcome}<p class:success-outcome={outcome.startsWith('Эталон совпал')} class="notice">{outcome}</p>{/if}

          {#key `${selected.id}:${selectedMode}`}
            {@const trial = makeTrial(selected, selectedMode)}
            {@const logger = new TrialEventLogger(trial, () => undefined, () => performance.now(), () => undefined, false)}
            {#if selectedMode === 'M1'}
              <M1Workbench {trial} {records} {referenceDate} {logger} validationTaskId={selected.id} onAttempt={handleAttempt} onBusy={(busy, text = '') => busyText = busy ? text : ''} />
            {:else if selectedMode === 'M2'}
              <M2Workbench {trial} help={manualHelp} {logger} validationTaskId={selected.id} onAttempt={handleAttempt} onBusy={(busy, text = '') => busyText = busy ? text : ''} />
            {:else if selectedMode === 'M3'}
              <M3Workbench {trial} {logger} validationTaskId={selected.id} onAttempt={handleAttempt} onBusy={(busy, text = '') => busyText = busy ? text : ''} />
            {:else if selectedMode === 'M4'}
              <M4Workbench {trial} {logger} validationTaskId={selected.id} onAttempt={handleAttempt} onBusy={(busy, text = '') => busyText = busy ? text : ''} />
            {:else}
              <M5Workbench {trial} {logger} validationTaskId={selected.id} onAttempt={handleAttempt} onBusy={(busy, text = '') => busyText = busy ? text : ''} />
            {/if}
          {/key}

          <details class="admin-reference">
            <summary>Показать эталонный Query и результат</summary>
            <div class="admin-reference-body">
              <QueryInspector query={selected.query} />
              <ResultsTable result={selected.result} query={selected.query} />
            </div>
          </details>
        </section>
      {/if}
    </div>
  {/if}
</main>

{#if introOpen}
  <IntroDeck oncomplete={() => introOpen = false} onclose={() => introOpen = false} />
{/if}

<style>
  .admin-mode-tabs { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; margin: 6px 0 8px; }
  .admin-mode-tabs button { border: 1px solid #cbd5e1; background: #f8fafc; color: #475569; }
  .admin-mode-tabs button.active { background: #172f70; color: white; border-color: #172f70; }
  .admin-mode-note { margin-bottom: 16px; }
  .success-outcome { background: #ecfdf5; color: #166534; border: 1px solid #86efac; }
  .admin-reference { margin-top: 22px; border: 1px solid #dbe3ef; border-radius: 14px; background: #f8fafc; }
  .admin-reference > summary { cursor: pointer; padding: 14px 16px; font-weight: 750; color: #334155; }
  .admin-reference-body { padding: 0 16px 16px; }
  @media (max-width: 760px) { .admin-mode-tabs { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
</style>

import { api, ApiError, errorMessage } from './api';
import type { TrialView } from './types';

type InputAction = 'click' | 'keydown' | 'pointermove' | 'scroll' | 'change';
type EventKind = 'input' | 'focus_lost' | 'focus_gained' | 'request_started' | 'request_finished' | 'speech_started' | 'speech_finished' | 'navigation';

interface ClientEvent {
  event_id: string;
  sequence: number;
  offset_ms: number;
  kind: EventKind;
  target: string;
  action?: InputAction;
  x?: number;
  y?: number;
  key?: string;
  request_id?: string;
}

function targetName(target: EventTarget | null) {
  if (!(target instanceof Element)) return '';
  const tracked = target.closest<HTMLElement>('[data-track]');
  if (tracked?.dataset.track) return tracked.dataset.track.slice(0, 120);
  const element = target as HTMLElement;
  const id = element.id ? `#${element.id}` : '';
  const name = element.getAttribute('name') ? `[name=${element.getAttribute('name')}]` : '';
  return `${element.tagName.toLowerCase()}${id}${name}`.slice(0, 120);
}

export class TrialEventLogger {
  private sequence: number;
  private lastOffset: number;
  private buffer: ClientEvent[] = [];
  private flushPromise: Promise<void> | null = null;
  private interval: ReturnType<typeof setInterval> | null = null;
  private cleanup: Array<() => void> = [];
  private lastPointerAt = 0;
  private lastScrollAt = 0;
  private anchorTime: number;
  private anchorOffset: number;
  private submissionPending = false;
  private closed = false;
  private readonly durationLimit: number;
  private terminalOffset: number | null = null;

  constructor(private trial: TrialView, private onWarning: (message: string) => void = () => undefined,
              private monotonicNow: () => number = () => performance.now(),
              private onTerminal: () => void = () => undefined,
              private enabled = true) {
    this.sequence = trial.next_event_sequence;
    this.lastOffset = trial.last_event_offset_ms;
    this.anchorOffset = Math.max(this.lastOffset, trial.elapsed_since_start_ms);
    this.anchorTime = this.monotonicNow();
    this.durationLimit = trial.deadline_ms !== null && trial.started_ms !== null
      ? trial.deadline_ms - trial.started_ms : Number.POSITIVE_INFINITY;
  }

  start() {
    if (!this.enabled) return;
    const onClick = (event: MouseEvent) => this.input('click', event.target, { x: event.clientX, y: event.clientY });
    const onChange = (event: Event) => this.input('change', event.target);
    const onKey = (event: KeyboardEvent) => this.input('keydown', event.target, { key: event.key.slice(0, 32) });
    const onPointer = (event: PointerEvent) => {
      const now = performance.now();
      if (now - this.lastPointerAt < 250) return;
      this.lastPointerAt = now;
      this.input('pointermove', event.target, { x: event.clientX, y: event.clientY });
    };
    const onScroll = (event: Event) => {
      const now = performance.now();
      if (now - this.lastScrollAt < 250) return;
      this.lastScrollAt = now;
      this.input('scroll', event.target);
    };
    const onBlur = () => { if (!this.closed) this.push('focus_lost', 'window'); };
    const onFocus = () => { if (!this.closed) this.push('focus_gained', 'window'); };

    document.addEventListener('click', onClick, true);
    document.addEventListener('change', onChange, true);
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('pointermove', onPointer, true);
    document.addEventListener('scroll', onScroll, true);
    window.addEventListener('blur', onBlur);
    window.addEventListener('focus', onFocus);
    this.cleanup = [
      () => document.removeEventListener('click', onClick, true),
      () => document.removeEventListener('change', onChange, true),
      () => document.removeEventListener('keydown', onKey, true),
      () => document.removeEventListener('pointermove', onPointer, true),
      () => document.removeEventListener('scroll', onScroll, true),
      () => window.removeEventListener('blur', onBlur),
      () => window.removeEventListener('focus', onFocus)
    ];
    this.interval = setInterval(() => { void this.flushSafely(); }, 2000);
  }

  stop() {
    this.closed = true;
    for (const dispose of this.cleanup) dispose();
    this.cleanup = [];
    if (this.interval) clearInterval(this.interval);
    this.interval = null;
    return this.flushSafely();
  }

  private offset() {
    // Local wall clocks need not agree with the server's UTC clock.
    const estimated = this.anchorOffset + this.monotonicNow() - this.anchorTime;
    return Math.min(this.durationLimit, Math.max(this.lastOffset, Math.floor(estimated), 0));
  }

  remainingMilliseconds() { return Math.max(0, this.durationLimit - this.offset()); }

  private push(kind: EventKind, target: string, details: Partial<ClientEvent> = {}, forcedOffset?: number) {
    if (!this.enabled || this.closed) return;
    const offset = Math.min(this.durationLimit, Math.max(this.lastOffset, forcedOffset ?? this.offset()));
    this.lastOffset = offset;
    this.buffer.push({ event_id: crypto.randomUUID(), sequence: this.sequence++, offset_ms: offset, kind, target, ...details });
    if (this.buffer.length >= 100) void this.flushSafely();
  }

  private input(action: InputAction, target: EventTarget | null, details: Partial<ClientEvent> = {}) {
    if (this.closed) return;
    this.push('input', targetName(target), { action, ...details });
  }

  navigation(target: string) {
    if (this.closed) return;
    this.push('navigation', target.slice(0, 120));
  }

  speechStarted(target = 'speech-recognition') {
    if (this.closed) return;
    this.push('speech_started', target.slice(0, 120));
  }

  speechFinished(target = 'speech-recognition') {
    if (this.closed) return;
    this.push('speech_finished', target.slice(0, 120));
  }

  beginSubmission(requestId: string, target: string) {
    this.submissionPending = true;
    this.requestStarted(requestId, target);
  }

  endSubmission(requestId: string, target: string, finishedMs?: number, terminal = false) {
    const offset = finishedMs !== undefined && this.trial.started_ms !== null
      ? Math.max(0, finishedMs - this.trial.started_ms) : this.offset();
    if (terminal) {
      const end = Math.min(this.durationLimit, offset);
      this.terminalOffset = end;
      // Actions while a terminal response was travelling are outside the trial.
      // Pending batches have not been uploaded while the outcome was unknown.
      this.buffer = this.buffer.filter((event) => event.offset_ms <= end);
      this.lastOffset = Math.min(this.lastOffset, end);
    }
    this.requestFinished(requestId, target, terminal ? offset : this.offset());
    this.anchorOffset = this.lastOffset;
    this.anchorTime = this.monotonicNow();
    this.closed = this.closed || terminal;
    this.submissionPending = false;
  }

  async flushSafely() {
    try { await this.flush(); }
    catch (error) {
      this.onWarning(`Журнал действий сохранён не полностью. ${errorMessage(error)} Это не оценка вашего ответа.`);
    }
  }

  requestStarted(requestId: string, target: string, forcedOffset?: number) {
    this.push('request_started', target.slice(0, 120), { request_id: requestId }, forcedOffset);
  }

  requestFinished(requestId: string, target: string, forcedOffset?: number) {
    this.push('request_finished', target.slice(0, 120), { request_id: requestId }, forcedOffset);
  }

  async measure<T>(target: string, fn: () => Promise<T>) {
    const requestId = crypto.randomUUID();
    this.requestStarted(requestId, target);
    try {
      return await fn();
    } finally {
      this.requestFinished(requestId, target);
    }
  }

  async flush() {
    if (!this.enabled || this.submissionPending) return;
    if (this.flushPromise) {
      await this.flushPromise;
      if (this.buffer.length) await this.flush();
      return;
    }
    if (!this.buffer.length) return;
    let batch = this.buffer.splice(0, 500);
    const send = () => api(`/api/trials/${this.trial.id}/events`, {
      method: 'POST', body: JSON.stringify({ events: batch })
    });
    const pending = (async () => {
      try { await send(); }
      catch (error) {
        if (!(error instanceof ApiError) || error.code !== 'event_outside_trial') throw error;
        const latest = await api<TrialView>(`/api/trials/${this.trial.id}`);
        if (latest.ended_ms === null || latest.started_ms === null) throw error;
        // The server can finish this trial in another tab or while a preview is in flight.
        // Never relax server bounds or rewrite the timestamps of valid events.
        this.terminalOffset = latest.ended_ms - latest.started_ms;
        this.closed = true;
        batch = batch.filter((event) => event.offset_ms <= this.terminalOffset!);
        this.buffer = this.buffer.filter((event) => event.offset_ms <= this.terminalOffset!);
        this.onTerminal();
        if (batch.length) await send();
      }
    })();
    this.flushPromise = pending;
    try {
      await pending;
    } catch (error) {
      this.buffer = [...batch, ...this.buffer].filter((event) => this.terminalOffset === null || event.offset_ms <= this.terminalOffset);
      throw error;
    } finally {
      if (this.flushPromise === pending) this.flushPromise = null;
    }
    if (this.buffer.length) await this.flush();
  }

}

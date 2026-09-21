import { api } from './api';
import type { TrialView } from './types';

type InputAction = 'click' | 'keydown' | 'pointermove' | 'scroll' | 'change';
type EventKind = 'input' | 'focus_lost' | 'focus_gained' | 'request_started' | 'request_finished' | 'navigation';

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

  constructor(private trial: TrialView) {
    this.sequence = trial.next_event_sequence;
    this.lastOffset = trial.last_event_offset_ms;
  }

  start() {
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
    const onBlur = () => this.push('focus_lost', 'window');
    const onFocus = () => this.push('focus_gained', 'window');

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
    this.interval = setInterval(() => { void this.flush().catch(() => undefined); }, 2000);
  }

  stop() {
    for (const dispose of this.cleanup) dispose();
    this.cleanup = [];
    if (this.interval) clearInterval(this.interval);
    this.interval = null;
    void this.flush().catch(() => undefined);
  }

  private offset() {
    const estimated = this.trial.started_ms === null ? 0 : Date.now() - this.trial.started_ms;
    return Math.max(this.lastOffset, estimated, 0);
  }

  private push(kind: EventKind, target: string, details: Partial<ClientEvent> = {}, forcedOffset?: number) {
    const offset = Math.max(this.lastOffset, forcedOffset ?? this.offset());
    this.lastOffset = offset;
    this.buffer.push({ event_id: crypto.randomUUID(), sequence: this.sequence++, offset_ms: offset, kind, target, ...details });
    if (this.buffer.length >= 100) void this.flush().catch(() => undefined);
  }

  private input(action: InputAction, target: EventTarget | null, details: Partial<ClientEvent> = {}) {
    this.push('input', targetName(target), { action, ...details });
  }

  navigation(target: string) {
    this.push('navigation', target.slice(0, 120));
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
    if (this.flushPromise) {
      await this.flushPromise;
      if (this.buffer.length) await this.flush();
      return;
    }
    if (!this.buffer.length) return;
    const batch = this.buffer.splice(0, 500);
    const pending = api(`/api/trials/${this.trial.id}/events`, {
      method: 'POST',
      body: JSON.stringify({ events: batch })
    }).then(() => undefined);
    this.flushPromise = pending;
    try {
      await pending;
    } catch (error) {
      this.buffer = [...batch, ...this.buffer];
      throw error;
    } finally {
      if (this.flushPromise === pending) this.flushPromise = null;
    }
    if (this.buffer.length) await this.flush();
  }

}

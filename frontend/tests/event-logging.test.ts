// Pure telemetry / HTTP contract tests. No DOM, browser or rendered component.
import { afterEach, expect, spyOn, test } from 'bun:test';
import { TrialEventLogger } from '../src/lib/event-logger';
import { submitTrialAttempt } from '../src/lib/trial-actions';
import type { AttemptView, Query, TrialView } from '../src/lib/types';

const trial = {
  id: 'trial', started_ms: 10000, deadline_ms: 310000,
  next_event_sequence: 0, last_event_offset_ms: 0, elapsed_since_start_ms: 500,
  metrics: { actual: { elapsed_ms: 500 } }
} as TrialView;
const fetchSpy = spyOn(globalThis, 'fetch');
const dateSpy = spyOn(Date, 'now');
afterEach(() => { fetchSpy.mockReset(); dateSpy.mockReset(); });

function capture() {
  const batches: Array<Array<{ kind: string; target: string; offset_ms: number }>> = [];
  fetchSpy.mockImplementation(async (_url, init) => {
    batches.push(JSON.parse(String(init?.body)).events);
    return Response.json({ accepted: batches.at(-1)?.length });
  });
  return batches;
}

test('client wall clock changes do not affect scientific event offsets', async () => {
  const batches = capture();
  let monotonic = 0;
  const logger = new TrialEventLogger(trial, undefined, () => monotonic);
  dateSpy.mockReturnValue(9000000000000);
  logger.navigation('first');
  monotonic = 250;
  dateSpy.mockReturnValue(1);
  logger.navigation('second');
  await logger.flush();
  expect(batches[0].map((event) => event.offset_ms)).toEqual([500, 750]);
});

test('reload resumes the server elapsed time even without recent input', async () => {
  const batches = capture();
  const logger = new TrialEventLogger({ ...trial, elapsed_since_start_ms: 25000, last_event_offset_ms: 2000, next_event_sequence: 3 }, undefined, () => 0);
  logger.navigation('resume');
  await logger.flush();
  expect(batches[0][0].offset_ms).toBe(25000);
  expect(logger.remainingMilliseconds()).toBe(275000);
});

test('response latency does not append actions beyond the completed trial', async () => {
  const batches = capture();
  let monotonic = 0;
  const logger = new TrialEventLogger(trial, undefined, () => monotonic);
  logger.beginSubmission('request', 'attempts');
  monotonic = 5000;
  logger.navigation('while-waiting');
  logger.endSubmission('request', 'attempts', 11000, true);
  logger.navigation('after-completion');
  await logger.flush();
  expect(batches[0].map((event) => event.kind)).toEqual(['request_started', 'request_finished']);
  expect(batches[0].map((event) => event.offset_ms)).toEqual([500, 1000]);
});

test('accepted answer stays successful when telemetry upload fails', async () => {
  const warnings: string[] = [];
  const batches: Array<Array<{ kind: string }>> = [];
  const saved = { id: 'attempt', trial_id: trial.id, correct: true, trial_status: 'correct', finished_ms: 11000 } as AttemptView;
  fetchSpy.mockImplementation(async (url, init) => {
    if (String(url).endsWith('/attempts')) return Response.json(saved);
    batches.push(JSON.parse(String(init?.body)).events);
    return Response.json({ error: { code: 'event_sequence', message: 'Журнал обновлён в другой вкладке.' } }, { status: 409 });
  });
  const logger = new TrialEventLogger(trial, (message) => warnings.push(message), () => 0);
  expect(await submitTrialAttempt(trial, {} as Query, logger, 'request')).toEqual(saved);
  expect(warnings).toHaveLength(1);
  expect(warnings[0]).toContain('не оценка вашего ответа');
  expect(batches[0].filter((event) => event.kind === 'request_finished')).toHaveLength(1);
});

test('a trial completed elsewhere keeps its valid event prefix and stops recording', async () => {
  let monotonic = 0;
  const sent: Array<Array<{ offset_ms: number }>> = [];
  fetchSpy.mockImplementation(async (url, init) => {
    if (!String(url).endsWith('/events')) return Response.json({ ...trial, status: 'correct', ended_ms: 11000 });
    const batch = JSON.parse(String(init?.body)).events;
    sent.push(batch);
    if (batch.some((event: { offset_ms: number }) => event.offset_ms > 1000)) {
      return Response.json({ error: { code: 'event_outside_trial', message: 'За пределами пробы' } }, { status: 409 });
    }
    return Response.json({ accepted: batch.length });
  });
  const logger = new TrialEventLogger(trial, undefined, () => monotonic);
  logger.navigation('before-completion');
  monotonic = 1500;
  logger.navigation('after-completion-in-other-tab');
  await logger.flush();
  logger.navigation('must-not-be-recorded');
  await logger.flush();
  expect(sent).toHaveLength(2);
  expect(sent[1].map((event) => event.offset_ms)).toEqual([500]);
});

test('an active trial with invalid event time is not silently clipped', async () => {
  let fail = true;
  const sent: unknown[] = [];
  fetchSpy.mockImplementation(async (url, init) => {
    if (!String(url).endsWith('/events')) return Response.json({ ...trial, status: 'active', ended_ms: null });
    sent.push(JSON.parse(String(init?.body)).events);
    return fail ? Response.json({ error: { code: 'event_outside_trial', message: 'Invalid time' } }, { status: 409 })
      : Response.json({ accepted: 1 });
  });
  const logger = new TrialEventLogger(trial, undefined, () => 0);
  logger.navigation('retain-this-event');
  await expect(logger.flush()).rejects.toThrow('Invalid time');
  fail = false;
  await logger.flush();
  expect(sent[1]).toEqual(sent[0]);
});

import { api } from './api';
import type { AttemptView, PreviewView, Query, TrialView } from './types';
import type { TrialEventLogger } from './event-logger';

async function submitAndRecord(trial: TrialView, payload: { query: Query } | { text: string },
                               logger: TrialEventLogger, requestId: string, endpoint: string) {
  logger.beginSubmission(requestId, endpoint);
  let response: AttemptView;
  try {
    response = await api<AttemptView>(`/api/trials/${trial.id}/${endpoint}`, {
      method: 'POST',
      body: JSON.stringify({ request_id: requestId, ...payload })
    });
  } catch (error) {
    logger.endSubmission(requestId, endpoint);
    await logger.flushSafely();
    throw error;
  }
  logger.endSubmission(requestId, endpoint, response.finished_ms, response.trial_status !== 'active');
  // Telemetry failure must never turn an already saved answer into a failed submission.
  await logger.flushSafely();
  return response;
}

export async function submitTrialAttempt(trial: TrialView, query: Query, logger: TrialEventLogger, requestId: string) {
  return submitAndRecord(trial, { query }, logger, requestId, 'attempts');
}

export async function submitM3Attempt(trial: TrialView, text: string, logger: TrialEventLogger, requestId: string) {
  return submitAndRecord(trial, { text }, logger, requestId, 'm3-attempts');
}

export async function submitM4Attempt(trial: TrialView, text: string, logger: TrialEventLogger, requestId: string) {
  return submitAndRecord(trial, { text }, logger, requestId, 'm4-attempts');
}

export async function submitM5Attempt(trial: TrialView, text: string, logger: TrialEventLogger, requestId: string) {
  return submitAndRecord(trial, { text }, logger, requestId, 'm5-attempts');
}

export async function previewTextRequest(trial: TrialView, text: string, logger: TrialEventLogger, requestId: string, endpoint: 'm3-preview' | 'm4-preview' | 'm5-preview') {
  logger.requestStarted(requestId, endpoint);
  try {
    return await api<PreviewView>(`/api/trials/${trial.id}/${endpoint}`, {
      method: 'POST', body: JSON.stringify({ request_id: requestId, text })
    });
  } finally {
    logger.requestFinished(requestId, endpoint);
    await logger.flushSafely();
  }
}

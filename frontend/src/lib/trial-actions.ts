import { api } from './api';
import type { AttemptView, Query, TrialView } from './types';
import type { TrialEventLogger } from './event-logger';

export async function submitTrialAttempt(trial: TrialView, query: Query, logger: TrialEventLogger, requestId: string) {
  logger.requestStarted(requestId, 'attempt');
  try {
    const response = await api<AttemptView>(`/api/trials/${trial.id}/attempts`, {
      method: 'POST',
      body: JSON.stringify({ request_id: requestId, query })
    });
    const finishOffset = trial.started_ms === null ? undefined : Math.max(0, response.finished_ms - trial.started_ms);
    logger.requestFinished(requestId, 'attempt', finishOffset);
    await logger.flush();
    return response;
  } catch (error) {
    logger.requestFinished(requestId, 'attempt');
    await logger.flush().catch(() => undefined);
    throw error;
  }
}

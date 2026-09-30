import { accessContext } from './access-context';

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const context = accessContext();
  if (context) headers.set('X-Access-Context', context);
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(path, { ...init, headers, credentials: 'same-origin' });
  if (!response.ok) {
    let code = 'request_failed';
    let message = `Ошибка запроса (${response.status}).`;
    try {
      const body = await response.json() as { error?: { code?: string; message?: string }; detail?: unknown };
      if (body.error?.code) code = body.error.code;
      if (body.error?.message) message = body.error.message;
    } catch { /* keep generic message */ }
    throw new ApiError(response.status, code, message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function jsonBody(value: unknown): RequestInit {
  return { body: JSON.stringify(value) };
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Неизвестная ошибка.';
}

export function workbenchError(error: unknown): string {
  const message = errorMessage(error);
  if (!(error instanceof ApiError)) return message;
  const guidance: Record<string, string> = {
    invalid_tag: 'Проверьте незавершённое условие: сначала выберите поле, затем оператор/значение и подтвердите условие.',
    invalid_operations: 'Проверьте цепочку операций. Для поиска самой большой/маленькой группы нужны: Группировка → Итог → Экстремум.',
    invalid_filter_value: 'Проверьте тип значения: даты вводятся как ГГГГ-ММ-ДД, оценка — целым числом, альтернативы — отдельными значениями.',
    invalid_operator: 'Этот оператор не подходит выбранному полю. Сравнения >, <, ≥, ≤ доступны для дат и оценки в часах.',
    invalid_null_filter: '«Отсутствует/указан» используется только для исполнителя и дедлайна и не требует дополнительного значения.',
    ambiguous_logic: 'ИЛИ можно использовать только между альтернативами одного и того же поля. Для независимых условий используйте И.',
    llm_invalid_query: 'RouterAI распознал формулировку, но собрал несовместимые операции. Уточните фильтры, группировку, итог и требуемый экстремум.',
    llm_invalid_response: 'RouterAI вернул ответ не в ожидаемом структурированном формате. Повторите предпросмотр; итоговая попытка при этом не расходуется.',
    llm_unavailable: 'RouterAI сейчас не ответил. Повторите предпросмотр; итоговая попытка не расходуется.',
    agent_invalid_response: 'Агент получил некорректный ответ RouterAI. Повторите предпросмотр; итоговая попытка не расходуется.',
    agent_unavailable: 'RouterAI сейчас недоступен. Повторите предпросмотр; итоговая попытка не расходуется.',
    agent_provider_error: 'RouterAI вернул ошибку провайдера. Повторите предпросмотр; итоговая попытка не расходуется.',
    trial_not_active: 'Время пробы истекло либо проба уже завершена. Обновите состояние сессии.',
    request_in_progress: 'Предыдущий предпросмотр с этим идентификатором ещё выполняется. Дождитесь ответа или повторите после завершения запроса.'
  };
  return guidance[error.code] ? `${message} ${guidance[error.code]}` : `${message} Код ошибки: ${error.code}.`;
}

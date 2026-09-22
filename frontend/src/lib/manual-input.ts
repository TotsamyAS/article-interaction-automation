import type { TagBuilder } from './types';

export interface ManualClause { builder: TagBuilder; operator: string; values: string[] }
const fold = (text: string) => text.trim().toLocaleLowerCase('ru');

export function exclusionValue(raw: string): string | null {
  const match = /^(?:НЕ|Исключить)(?:\s+|$)(.*)$/i.exec(raw.trim());
  return match ? match[1].trim() : null;
}

// Field selection is a draft, not a completed condition requiring a value.
export function readField(raw: string, builders: TagBuilder[], exclude = false): Pick<ManualClause, 'builder' | 'operator'> | null {
  const excluded = exclusionValue(raw);
  const label = (excluded ?? raw).trim().replace(/:$/, '').trim();
  const builder = builders.find((item) => fold(item.label) === fold(label));
  if (!builder) return null;
  const operator = exclude || excluded !== null ? '!=' : builder.operators[0];
  if (!builder.operators.includes(operator)) throw new Error(`«${builder.label}» нельзя исключить. Выберите поле фильтра.`);
  return { builder, operator };
}

export function readClause(raw: string, builders: TagBuilder[]): ManualClause | null {
  if (/[()]/.test(raw)) throw new Error('Скобки в условиях не поддерживаются. Для группировки записей введите «Группировка».');
  const match = /^\s*([^:!=<>]+?)\s*(:|!=|>=|<=|=|>|<)\s*(.+?)\s*$/.exec(raw);
  if (!match) return null;
  const field = readField(match[1], builders);
  if (!field) return null;
  const { builder } = field;
  const excluded = exclusionValue(match[3]);
  const negate = field.operator === '!=' || excluded !== null;
  if (negate && ![':', '='].includes(match[2])) throw new Error('После «НЕ» используйте значение без дополнительного сравнения.');
  if (field.operator === '!=' && excluded !== null) throw new Error('Укажите «НЕ» один раз: перед полем или перед значением.');
  const operator = negate ? '!=' : match[2] === '=' ? ':' : match[2];
  const rawValues = excluded ?? match[3];
  if (!builder.operators.includes(operator)) throw new Error(`Сравнение «${operator}» недоступно для «${builder.label}».`);
  if (operator === '!=' && /\s+ИЛИ(?:\s|$)/i.test(rawValues)) {
    throw new Error('Для нескольких исключений используйте «И НЕ», например «Низкий И НЕ Средний».');
  }
  if (operator !== '!=' && /\s+И НЕ(?:\s|$)/i.test(rawValues)) throw new Error('Связка «И НЕ» применяется только к исключениям (!=).');
  const values = rawValues.split(/\s+ИЛИ(?:\s+|$)|\s+И НЕ(?:\s+|$)|\//i).map((part) => part.trim());
  if (values.some((part) => !part)) throw new Error('Введите значение после связки.');
  if (values.some((part) => /[:=<>!]|\s+И\s/i.test(part))) {
    throw new Error('«ИЛИ» связывает значения одного поля. Для другого поля используйте «И».');
  }
  if (values.length > 1 && (!builder.alternatives || ![':', '!='].includes(operator)
      || values.some((part) => builder.exclusive_values.some((single) => fold(single) === fold(part))))) {
    throw new Error(`Эти значения «${builder.label}» нельзя объединить в одно условие.`);
  }
  return { builder, operator, values: [...new Map(values.map((part) => [fold(part), part])).values()] };
}

export function writeClause(clause: ManualClause): string {
  return `${clause.builder.label}${clause.operator === ':' ? ':' : ' ' + clause.operator} ${clause.values.join(' / ')}`;
}

export function canExtend(clause: ManualClause): boolean {
  return clause.builder.alternatives && [':', '!='].includes(clause.operator)
    && !clause.values.some((part) => clause.builder.exclusive_values.some((single) => fold(single) === fold(part)));
}

export function extendClause(tag: string, raw: string, builders: TagBuilder[]): string {
  const clause = readClause(tag, builders);
  if (!clause || !canExtend(clause)) throw new Error('Выберите условие с несколькими допустимыми значениями.');
  const combined = readClause(`${writeClause(clause)} / ${raw}`, builders);
  if (!combined) throw new Error('Введите значение того же поля.');
  return writeClause(combined);
}

export function readConditions(raw: string, builders: TagBuilder[]): string[] {
  if (/\s+И\s*$/i.test(raw)) throw new Error('Введите новое условие после «И».');
  const parts = raw.split(/\s+И\s+(?!НЕ(?:\s|$))|\s+И\s+(?=НЕ\s+[^:!=<>]+?\s*[:!=<>])/i);
  return parts.map((part) => {
    const clause = readClause(part, builders);
    if (!clause) throw new Error('Введите название поля или условие «Поле: значение».');
    return writeClause(clause);
  });
}

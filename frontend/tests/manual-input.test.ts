// Pure syntax/serialization checks. No DOM, focus automation or rendered UI.
import { expect, test } from 'bun:test';
import { exclusionValue, extendClause, readClause, readConditions, readField, writeClause } from '../src/lib/manual-input';
import type { TagBuilder } from '../src/lib/types';

const builders: TagBuilder[] = [
  { label: 'Статус', kind: 'filter', operators: [':', '!='], values: ['В работе', 'На ревью'], alternatives: true, exclusive_values: [], placeholder: '' },
  { label: 'Приоритет', kind: 'filter', operators: [':', '!='], values: ['Высокий', 'Средний', 'Низкий'], alternatives: true, exclusive_values: [], placeholder: '' },
  { label: 'Группировка', kind: 'action', operators: [':'], values: ['эпик', 'спринт'], alternatives: false, exclusive_values: [], placeholder: '' },
  { label: 'Итог', kind: 'action', operators: [':'], values: ['количество'], alternatives: false, exclusive_values: [], placeholder: '' },
];

test('a completed tag can be extended repeatedly without creating a second condition', () => {
  const saved = writeClause(readClause('Статус: В работе', builders)!);
  const extended = extendClause(saved, 'На ревью', builders);
  expect(extended).toBe('Статус: В работе / На ревью');
  expect(extendClause(extended, 'В работе', builders)).toBe(extended);
});

test('inline ИЛИ has the same representation as continuing a completed tag', () => {
  expect(readConditions('Статус: В работе ИЛИ На ревью', builders)).toEqual([
    extendClause('Статус: В работе', 'На ревью', builders)
  ]);
});

test('И starts a separate condition while actions keep their own operation', () => {
  expect(readConditions('Статус: В работе ИЛИ На ревью И Приоритет: Высокий', builders)).toEqual([
    'Статус: В работе / На ревью', 'Приоритет: Высокий'
  ]);
  expect(readConditions('Группировка: эпик И Итог: количество', builders)).toEqual(['Группировка: эпик', 'Итог: количество']);
});

test('parentheses and ИЛИ across different fields are never silently flattened', () => {
  expect(() => readConditions('(Статус: В работе)', builders)).toThrow('Скобки');
  expect(() => extendClause('Статус: В работе', 'Приоритет: Высокий', builders)).toThrow('одного поля');
});

test('incomplete alternatives stay invalid instead of becoming literal field values', () => {
  expect(() => readConditions('Статус: В работе ИЛИ', builders)).toThrow('после связки');
  expect(() => readConditions('Статус: В работе И', builders)).toThrow();
});

test('exclusions use И НЕ rather than changing their meaning to OR', () => {
  expect(readConditions('Приоритет != Низкий И НЕ Средний', builders)).toEqual(['Приоритет != Низкий / Средний']);
  expect(() => readConditions('Приоритет != Низкий ИЛИ Средний', builders)).toThrow('И НЕ');
});

test('НЕ and Исключить compile to the existing exclusion contract', () => {
  for (const raw of ['НЕ Приоритет: Низкий', 'Исключить Приоритет: Низкий', 'Приоритет: НЕ Низкий']) {
    expect(readConditions(raw, builders)).toEqual(['Приоритет != Низкий']);
  }
  expect(readConditions('Статус: В работе И НЕ Приоритет: Низкий', builders)).toEqual([
    'Статус: В работе', 'Приоритет != Низкий'
  ]);
  expect(readConditions('НЕ Приоритет: Низкий И НЕ Средний', builders)).toEqual(['Приоритет != Низкий / Средний']);
});

test('a bare field is an unfinished draft, including exclusion field selection', () => {
  for (const raw of ['Статус', 'статус', ' Статус: ']) {
    expect(readField(raw, builders)).toEqual({ builder: builders[0], operator: ':' });
  }
  for (const raw of ['НЕ Приоритет', 'Исключить Приоритет']) {
    expect(readField(raw, builders)).toEqual({ builder: builders[1], operator: '!=' });
  }
  for (const keyword of ['НЕ', 'Исключить']) {
    expect(exclusionValue(keyword)).toBe('');
    const field = readField('Приоритет', builders, exclusionValue(keyword) !== null)!;
    expect(writeClause(readClause(`${field.builder.label} ${field.operator} Низкий`, builders)!)).toBe('Приоритет != Низкий');
  }
  expect(readField('Статус: В работе', builders)).toBeNull();
});

test('negation cannot silently invert actions, comparisons or double negation', () => {
  expect(() => readField('НЕ Группировка', builders)).toThrow('нельзя исключить');
  expect(() => readConditions('Исключить Итог: количество', builders)).toThrow('нельзя исключить');
  expect(() => readConditions('НЕ Приоритет != Низкий', builders)).toThrow('без дополнительного сравнения');
  expect(() => readConditions('НЕ Приоритет: НЕ Низкий', builders)).toThrow('один раз');
  expect(() => readConditions('Приоритет: НЕ', builders)).toThrow('Введите значение');
  expect(() => readConditions('НЕ Приоритет: Низкий ИЛИ Средний', builders)).toThrow('И НЕ');
});

test('legacy field aliases select the same draft and canonical new label', () => {
  const vocabulary: TagBuilder[] = [{ ...builders[0], label: 'Направление работ', aliases: ['Эпик'], values: ['Платежи'] }];
  expect(readField('Эпик', vocabulary)?.builder.label).toBe('Направление работ');
  expect(readConditions('НЕ Эпик: Платежи', vocabulary)).toEqual(['Направление работ != Платежи']);
});

<script lang="ts">
  import type { TagBuilder } from '../types';
  import { canExtend, exclusionValue, extendClause, readClause, readConditions, readField, writeClause } from '../manual-input';
  let { value = $bindable<string[]>([]), pending = $bindable(false), builders, placeholder = '', disabled = false, maxTags = 40 }: {
    value?: string[]; pending?: boolean; builders: TagBuilder[]; placeholder?: string; disabled?: boolean; maxTags?: number;
  } = $props();
  const id = $props.id();
  let input = $state('');
  let focused = $state(false);
  let activeIndex = $state(-1);
  let inputElement = $state<HTMLInputElement>();
  let message = $state('');
  let selected = $state<TagBuilder | null>(null);
  let operator = $state(':');
  let editing = $state<number | null>(null);
  let editingValue = $state<number | null>(null);
  let extending = $state(false);
  let expectField = $state(false);
  let excluding = $state(false);
  let continuation = $state<number | null>(null);
  let blurTimer: ReturnType<typeof setTimeout> | undefined;
  const items = $derived(value.map((tag, index) => ({ tag, index, clause: readClause(tag, builders) })));
  const current = $derived(continuation === null ? null : items[continuation]?.clause);
  const stage = $derived(selected ? 'value' : expectField || !value.length ? 'field' : 'join');
  const options = $derived.by(() => {
    const query = input.trim().toLocaleLowerCase('ru');
    let source: Array<{ text: string; raw: string; kind: string }>;
    if (selected) {
      const existing = extending && editing !== null ? items[editing].clause?.values ?? [] : [];
      source = selected.values.filter((item) => !existing.includes(item)
        && (!(extending || operator !== ':') || !selected?.exclusive_values.includes(item)))
        .map((item) => ({ text: item, raw: item, kind: 'значение' }));
      if (!extending && editingValue === null) source.push(...selected.operators.map((item) => ({ text: item === ':' ? '= · равно' : item === '!=' ? 'НЕ · исключить значение' : item, raw: item === '!=' ? 'НЕ' : item, kind: 'сравнение' })));
    } else {
      const available = excluding ? builders.filter((item) => item.kind === 'filter' && item.operators.includes('!=')) : builders;
      const ordered = stage === 'join' ? [...available.filter((item) => item.kind === 'action'), ...available.filter((item) => item.kind === 'filter')] : available;
      source = ordered.map((item) => ({ text: item.label, raw: item.label, kind: item.kind === 'action' ? 'действие' : 'поле' }));
      if (!excluding) source.unshift({ text: 'Исключить · НЕ', raw: 'Исключить', kind: 'исключение' });
      if (stage === 'join') {
        source.unshift({ text: 'И · новое условие', raw: 'И', kind: 'связка' });
        if (current && canExtend(current)) source.unshift({ text: current.operator === '!=' ? 'И НЕ · ещё одно исключение' : 'ИЛИ · ещё одно значение', raw: current.operator === '!=' ? 'И НЕ' : 'ИЛИ', kind: 'связка' });
      }
    }
    return source.filter((item) => !query || item.text.toLocaleLowerCase('ru').includes(query));
  });
  const stepHint = $derived(selected ? `${operator === '!=' ? 'Исключить: ' : ''}${selected.label} ${operator === '!=' ? '' : operator} — ${extending ? 'добавьте ещё одно значение' : 'введите значение'}`
    : excluding ? 'Исключить: выберите поле, затем значение — например, Приоритет → Низкий.'
    : stage === 'join' ? 'Продолжайте: И, ИЛИ или действие — например, Группировка.' : 'Введите поле или действие: Статус, Группировка, Итог…');
  $effect(() => { pending = !!input.trim() || selected !== null || editing !== null || expectField; });
  $effect(() => { activeIndex = options.length === 1 ? 0 : -1; });
  $effect(() => () => clearTimeout(blurTimer));
  function focus() { focused = true; clearTimeout(blurTimer); queueMicrotask(() => inputElement?.focus()); }
  function reset() { selected = null; operator = ':'; input = ''; editing = null; editingValue = null; extending = false; expectField = false; excluding = false; message = ''; }
  function save(tags: string[]) {
    const next = editing === null ? [...value, ...tags] : value.flatMap((tag, index) => index === editing ? tags : [tag]);
    if (next.length > maxTags) throw new Error(`Максимум ${maxTags} условий и действий.`);
    if (next.some((tag) => tag.length > 128)) throw new Error('Условие слишком длинное: максимум 128 символов.');
    const canonical = next.map((tag) => tag.toLocaleLowerCase('ru'));
    if (new Set(canonical).size !== canonical.length) throw new Error('Такое условие уже добавлено. Его можно изменить нажатием на тег.');
    continuation = editing === null ? next.length - 1 : editing + tags.length - 1;
    value = next; reset(); focus();
  }
  function chooseField(raw: string) {
    const field = readField(raw, builders, excluding);
    if (!field) return false;
    selected = field.builder; operator = field.operator; input = ''; expectField = false; editingValue = null; focus(); return true;
  }
  function beginAlternative(index: number) {
    const clause = items[index]?.clause;
    if (!clause || !canExtend(clause)) throw new Error('«ИЛИ» добавляет значение выбранного поля. Нажмите нужный тег или начните новое условие через «И».');
    reset(); selected = clause.builder; operator = clause.operator; editing = index; extending = true; continuation = index; focus();
  }
  function commit(raw: string) {
    if (disabled || !raw.trim()) return;
    message = '';
    const text = raw.trim();
    try {
      if (!selected) {
        const next = text.replace(/^И\s+/i, '');
        if (exclusionValue(next) === '' && !/^И\s+НЕ$/i.test(text)) { excluding = true; expectField = true; input = ''; focus(); return; }
        if (chooseField(next)) return;
        // «И НЕ Поле: значение» starts another filter; «И НЕ значение» extends an exclusion.
        if (/^И\s+НЕ\s+/i.test(text) && readClause(next, builders)) { save(readConditions(next, builders)); return; }
        const connective = /^(ИЛИ|И НЕ)(?:\s+(.*))?$/i.exec(text);
        if (connective) {
          if (continuation === null || !current) throw new Error('Сначала добавьте условие, затем его альтернативное значение.');
          if ((connective[1].toUpperCase() === 'ИЛИ') !== (current.operator === ':')) throw new Error('Для исключений используйте «И НЕ», для альтернативных значений — «ИЛИ».');
          beginAlternative(continuation);
          if (connective[2]) commit(connective[2]);
          return;
        }
        if (/^И$/i.test(text)) { reset(); expectField = true; focus(); return; }
        save(readConditions(excluding && exclusionValue(next) === null ? `НЕ ${next}` : next, builders)); return;
      }
      const comparisonToken = exclusionValue(text) === '' ? '!=' : text === '=' ? ':' : text;
      if (!extending && editingValue === null && selected.operators.includes(comparisonToken)) {
        operator = comparisonToken; input = ''; focus(); return;
      }
      let rawValue = text;
      const comparison = /^(!=|>=|<=|=|>|<)\s*(.+)$/.exec(text);
      if (comparison && !extending && editingValue === null) { operator = comparison[1] === '=' ? ':' : comparison[1]; rawValue = comparison[2]; }
      if (extending && editing !== null) { save([extendClause(value[editing], rawValue, builders)]); return; }
      if (editingValue !== null && editing !== null) {
        const original = items[editing].clause!;
        rawValue = original.values.map((part, index) => index === editingValue ? rawValue : part).join(' / ');
      }
      const clause = readClause(`${selected.label} ${operator} ${rawValue}`, builders);
      if (!clause) throw new Error('Введите значение выбранного поля.');
      save([writeClause(clause)]);
    } catch (error) { message = error instanceof Error ? error.message : 'Проверьте условие.'; focus(); }
  }
  function edit(index: number, part: 'field' | 'value', partIndex = 0) {
    const clause = items[index].clause;
    if (!clause || disabled) return;
    reset(); editing = index; continuation = index;
    if (part === 'field') { input = clause.builder.label; expectField = true; excluding = clause.operator === '!='; }
    else { selected = clause.builder; operator = clause.operator; editingValue = partIndex; input = clause.values[partIndex]; }
    focus();
  }
  function remove(index: number) { if (disabled) return; value = value.filter((_, i) => i !== index); reset(); continuation = value.length ? value.length - 1 : null; focus(); }
  function keydown(event: KeyboardEvent) {
    if (event.isComposing) return;
    if (['ArrowDown', 'ArrowUp'].includes(event.key) && options.length) {
      event.preventDefault(); focused = true;
      activeIndex = event.key === 'ArrowDown' ? (activeIndex + 1) % options.length : (activeIndex < 0 ? options.length - 1 : (activeIndex - 1 + options.length) % options.length);
      queueMicrotask(() => document.getElementById(`${id}-option-${activeIndex}`)?.scrollIntoView({ block: 'nearest' }));
    } else if (event.key === 'Enter' || (event.key === 'Tab' && !event.shiftKey && (input.trim() || activeIndex >= 0))) {
      event.preventDefault(); commit(activeIndex >= 0 ? options[activeIndex].raw : input);
    } else if (event.key === 'Escape') { reset(); focus(); }
    else if (event.key === 'Backspace' && !input && !selected && value.length) { event.preventDefault(); edit(value.length - 1, 'value'); }
  }
</script>

<div class="tag-input linked-tag-input" data-track="m2-query-builder">
  <div class="linked-clauses" aria-label="Собранный запрос">
    {#each items as item (item.tag)}
      {#if item.clause}
        {@const clause = item.clause}
        {#if clause.builder.kind === 'action'}<span class="action-token">ДЕЙСТВИЕ</span>
        {:else if items.slice(0, item.index).some((entry) => entry.clause?.builder.kind === 'filter')}<span class="logic-token">И</span>{/if}
        <div class="linked-clause" class:editing={editing === item.index} role="group" aria-label={item.tag}>
          {#if clause.operator === '!='}<span class="exclusion-token">Исключить</span>{/if}
          <button type="button" class="builder-field" {disabled} onclick={() => edit(item.index, 'field')} aria-label={'Изменить поле ' + clause.builder.label}>{clause.builder.label}</button>
          {#if clause.operator !== '!='}<span class="builder-operator">{clause.operator}</span>{/if}
          {#each clause.values as part, partIndex}
            {#if partIndex > 0}<span class="logic-token">{clause.operator === '!=' ? 'И НЕ' : 'ИЛИ'}</span>{/if}
            <button type="button" class="builder-value" {disabled} onclick={() => edit(item.index, 'value', partIndex)} aria-label={'Изменить значение ' + part}>{part}</button>
          {/each}
          {#if canExtend(clause)}<button type="button" class="logic-add" {disabled} onclick={() => beginAlternative(item.index)}>{clause.operator === '!=' ? '+ И НЕ' : '+ ИЛИ'}</button>{/if}
          <button type="button" class="clause-remove" {disabled} onclick={() => remove(item.index)} aria-label={'Удалить ' + item.tag}>×</button>
        </div>
      {/if}
    {/each}
  </div>
  <div class="builder-stage" data-stage={stage}>
    <p class="builder-step" id={id + '-hint'} aria-live="polite">{stepHint}</p>
    <div class="builder-autocomplete">
      <!-- This input stays mounted for every step, including И/ИЛИ and actions. -->
      <input bind:this={inputElement} bind:value={input} {disabled} type="text" maxlength="512" role="combobox"
        placeholder={selected?.placeholder ?? (excluding ? 'Какое поле исключить? Например, Приоритет' : stage === 'join' ? 'ИЛИ, И, Исключить, Группировка, Итог…' : placeholder)}
        aria-label="Единое поле конструктора запроса" aria-describedby={id + '-hint'} aria-autocomplete="list"
        aria-controls={id + '-options'} aria-expanded={focused && options.length > 0 && !disabled}
        aria-activedescendant={focused && activeIndex >= 0 ? `${id}-option-${activeIndex}` : undefined}
        onfocus={() => { clearTimeout(blurTimer); focused = true; }} onblur={() => blurTimer = setTimeout(() => focused = false, 150)} onkeydown={keydown}/>
      {#if focused && options.length && !disabled}
        <div class="tag-input-suggestions" id={id + '-options'} role="listbox" aria-label="Поля, связки и действия">
          {#each options as option, index (option.raw)}
            <button type="button" role="option" tabindex="-1" id={`${id}-option-${index}`} aria-selected={activeIndex === index} class:active={activeIndex === index}
              onmousedown={(event) => event.preventDefault()} onclick={() => commit(option.raw)}>{option.text}<span class="suggestion-kind">{option.kind}</span></button>
          {/each}
        </div>
      {/if}
    </div>
  </div>
  <p class="builder-help">Enter / Tab — подтвердить и продолжить здесь. Esc — отменить ввод. Группировка: введите «Группировка» → Enter → «эпик» → Enter. Затем добавьте «Итог», например «количество».</p>
  <p class="builder-help">Поле: «Статус» → Enter → «В работе» → Enter. Исключение: «Исключить» → «Приоритет» → «Низкий» (каждый шаг — Enter). Можно сразу: «НЕ Приоритет: Низкий». Ещё одно исключаемое значение — через «И НЕ».</p>
  {#if message}<p class="notice error" role="alert">{message}</p>{/if}
</div>

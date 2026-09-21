<script lang="ts">
  // Adapted from references/TagInput. Keep suggestion filtering, chips and keyboard
  // navigation; quantity entry and product-specific logging do not apply here.
  let { value = $bindable<string[]>([]), suggestions = [], placeholder = '', disabled = false,
    maxTags = 64, replaceWhenFull = false, ariaLabel = '', allowCustom = true, commitOnBlur = false,
    onValueChange, onInputChange }: {
    value?: string[]; suggestions?: readonly string[]; placeholder?: string; disabled?: boolean;
    maxTags?: number; replaceWhenFull?: boolean; ariaLabel?: string; allowCustom?: boolean; commitOnBlur?: boolean;
    onValueChange?: (value: string[]) => void; onInputChange?: (value: string) => void;
  } = $props();
  const componentId = $props.id();
  const suggestionsId = componentId + '-suggestions';
  let inputValue = $state('');
  let focused = $state(false);
  let activeSuggestionIndex = $state(0);
  let blurTimer: ReturnType<typeof setTimeout> | undefined;
  const availableSuggestions = $derived.by(() => {
    const query = inputValue.trim().toLocaleLowerCase('ru-RU');
    const selected = new Set(value.map((tag) => tag.toLocaleLowerCase('ru-RU')));
    return suggestions.filter((suggestion) => !selected.has(suggestion.toLocaleLowerCase('ru-RU')))
      .filter((suggestion) => !query || suggestion.toLocaleLowerCase('ru-RU').includes(query)).slice(0, 12);
  });
  $effect(() => { void availableSuggestions; activeSuggestionIndex = 0; });
  $effect(() => () => clearTimeout(blurTimer));
  function setValue(nextValue: string[]) {
    value = nextValue; onValueChange?.(nextValue);
  }
  function setInputValue(nextValue: string) {
    inputValue = nextValue; onInputChange?.(nextValue);
  }
  function addTag(raw: string) {
    if (disabled) return;
    const tag = raw.trim();
    if (!tag || tag.length > 128 || (!allowCustom && !suggestions.includes(tag))) return;
    if (value.some((entry) => entry.toLocaleLowerCase('ru-RU') === tag.toLocaleLowerCase('ru-RU'))) return;
    if (replaceWhenFull && maxTags === 1 && value.length === 1) setValue([tag]);
    else if (value.length < maxTags) setValue([...value, tag]);
  }
  function selectSuggestion(index = activeSuggestionIndex) {
    const suggestion = availableSuggestions[index];
    if (!suggestion) return;
    addTag(suggestion); setInputValue('');
  }
  function commitCustomInput() {
    if (!allowCustom) return;
    for (const part of inputValue.split(',')) addTag(part);
    setInputValue('');
  }
  function handleInput(event: Event) {
    setInputValue((event.currentTarget as HTMLInputElement).value);
    if (allowCustom && inputValue.includes(',')) {
      const parts = inputValue.split(',');
      for (const part of parts.slice(0, -1)) addTag(part);
      setInputValue(parts.at(-1) ?? '');
    }
  }
  function handleBlur() {
    if (commitOnBlur && inputValue.trim()) commitCustomInput();
    blurTimer = setTimeout(() => focused = false, 100);
  }
  function handleKeydown(event: KeyboardEvent) {
    if (event.key === 'Escape') { focused = false; return; }
    focused = true;
    if (['ArrowDown', 'ArrowUp'].includes(event.key) && availableSuggestions.length) {
      event.preventDefault();
      const step = event.key === 'ArrowDown' ? 1 : -1;
      activeSuggestionIndex = (activeSuggestionIndex + step + availableSuggestions.length) % availableSuggestions.length;
    } else if (['Enter', 'Tab'].includes(event.key) && availableSuggestions.length) {
      event.preventDefault(); selectSuggestion();
    } else if (event.key === 'Enter' || event.key === ',') {
      event.preventDefault(); commitCustomInput();
    } else if (event.key === 'Backspace' && !inputValue && value.length) {
      setValue(value.slice(0, -1));
    }
  }
</script>

<div class="tag-input" class:disabled>
  <div class="tag-input-values">
    {#each value as tag, index (tag)}
      <span class="tag-input-chip"><span>{tag}</span>
        <button type="button" {disabled} aria-label={'Удалить ' + tag}
          onclick={() => setValue(value.filter((_, currentIndex) => currentIndex !== index))}>×</button></span>
    {/each}
    <input type="text" value={inputValue} {placeholder} {disabled} role="combobox"
      aria-autocomplete="list" aria-controls={suggestionsId}
      aria-expanded={focused && availableSuggestions.length > 0}
      aria-activedescendant={focused && availableSuggestions.length ? suggestionsId + '-' + activeSuggestionIndex : undefined}
      aria-label={ariaLabel || placeholder}
      onfocus={() => { clearTimeout(blurTimer); focused = true; activeSuggestionIndex = 0; }}
      onblur={handleBlur}
      oninput={handleInput} onkeydown={handleKeydown} />
  </div>
  {#if focused && availableSuggestions.length && !disabled}
    <div id={suggestionsId} class="tag-input-suggestions" role="listbox" aria-label="Варианты">
      {#each availableSuggestions as suggestion, index (suggestion)}
        <button type="button" role="option" id={suggestionsId + '-' + index}
          class:active={index === activeSuggestionIndex} aria-selected={index === activeSuggestionIndex}
          onmouseenter={() => activeSuggestionIndex = index}
          onmousedown={(event) => event.preventDefault()} onclick={() => selectSuggestion(index)}>{suggestion}</button>
      {/each}
    </div>
  {/if}
</div>


<script lang="ts">
  import type { Snippet } from 'svelte';
  import type { TrialEventLogger } from '../event-logger';

  let { logger, track, children }: {
    logger: TrialEventLogger;
    track: string;
    children: Snippet;
  } = $props();

  function trackToggle(event: Event) {
    const details = event.currentTarget as HTMLDetailsElement;
    logger.navigation(`${track}:${details.open ? 'open' : 'closed'}`);
  }
</script>

<details class="mode-hint" data-track={track} ontoggle={trackToggle}>
  <summary>Подсказка по режиму</summary>
  <div class="mode-hint-body">
    {@render children()}
  </div>
</details>

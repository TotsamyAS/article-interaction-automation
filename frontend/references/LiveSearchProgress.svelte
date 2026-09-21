<script lang="ts">
  // Adapted from references/LiveSearchProgress: retain timer, indicators and track.
  // Import progress is unknown until the synchronous backend request completes.
  let { loading, statusText, progress = null }: {
    loading: boolean; statusText: string; progress?: number | null;
  } = $props();
  let elapsedSeconds = $state(0);
  const visualProgress = $derived(progress === null ? null : Math.min(100, Math.max(0, progress)));
  $effect(() => {
    if (!loading) return;
    elapsedSeconds = 0;
    const intervalId = window.setInterval(() => elapsedSeconds += 1, 1000);
    return () => window.clearInterval(intervalId);
  });
</script>

<div class="live-progress live-search-progress" role="status" aria-live="polite" aria-busy={loading}>
  <div class="live-progress-head">
    <div class="progress-title-row">
      {#if loading}<span class="progress-spinner" aria-hidden="true"></span>{/if}
      <strong class="progress-status-line">{statusText}</strong>
    </div>
    {#if visualProgress !== null}<span>{visualProgress}%</span>{/if}
  </div>
  <div class="live-progress-track" role="progressbar" aria-label={statusText}
    aria-valuemin="0" aria-valuemax="100" aria-valuenow={visualProgress ?? undefined}>
    <div class="live-progress-bar" class:indeterminate={visualProgress === null}
      style:width={visualProgress === null ? '35%' : visualProgress + '%'}></div>
  </div>
  <div class="progress-details">
    <span>До подтверждения источники не добавляются в корпус.</span>
    <span aria-live="off">{elapsedSeconds} с</span>
  </div>
</div>


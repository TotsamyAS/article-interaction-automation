<script lang="ts">
  let { loading, statusText, progress = null, detail = '' }: {
    loading: boolean; statusText: string; progress?: number | null; detail?: string;
  } = $props();
  let elapsedSeconds = $state(0);
  const visualProgress = $derived(progress === null ? null : Math.min(100, Math.max(0, progress)));
  $effect(() => {
    if (!loading) { elapsedSeconds = 0; return; }
    elapsedSeconds = 0;
    const intervalId = window.setInterval(() => elapsedSeconds += 1, 1000);
    return () => window.clearInterval(intervalId);
  });
</script>

<div class="live-progress" role="status" aria-live="polite" aria-busy={loading}>
  <div class="live-progress-head">
    <div class="progress-title-row">
      {#if loading}<span class="progress-spinner" aria-hidden="true"></span>{/if}
      <strong>{statusText}</strong>
    </div>
    {#if visualProgress !== null}<span>{visualProgress}%</span>{/if}
  </div>
  <div class="live-progress-track" role="progressbar" aria-label={statusText}
    aria-valuemin="0" aria-valuemax="100" aria-valuenow={visualProgress ?? undefined}>
    <div class="live-progress-bar" class:indeterminate={visualProgress === null}
      style:width={visualProgress === null ? '35%' : visualProgress + '%'}></div>
  </div>
  <div class="progress-details">
    <span>{detail}</span>
    {#if loading}<span aria-live="off">{elapsedSeconds} с</span>{/if}
  </div>
</div>

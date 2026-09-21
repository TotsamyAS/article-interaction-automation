<script lang="ts">
  let { tone = 'success', text, durationMs = 4500, resetKey = text, onDismiss }: {
    tone?: 'success' | 'error' | 'loading'; text: string; durationMs?: number; resetKey?: unknown; onDismiss?: () => void;
  } = $props();
  let visible = $state(true);
  $effect(() => {
    text; durationMs; resetKey;
    visible = true;
    if (durationMs <= 0) return;
    const timer = setTimeout(() => { visible = false; onDismiss?.(); }, durationMs);
    return () => clearTimeout(timer);
  });
</script>

{#if visible}
  <div class="app-toast" class:success={tone === 'success'} class:error={tone === 'error'} class:loading={tone === 'loading'}
    role={tone === 'error' ? 'alert' : 'status'} aria-live={tone === 'error' ? 'assertive' : 'polite'} aria-busy={tone === 'loading'}>
    {#if tone === 'loading'}<span class="progress-spinner" aria-hidden="true"></span>{/if}
    <span>{text}</span>
    {#if tone !== 'loading'}<button type="button" class="toast-dismiss" aria-label="Закрыть уведомление" onclick={() => { visible = false; onDismiss?.(); }}>×</button>{/if}
  </div>
{/if}

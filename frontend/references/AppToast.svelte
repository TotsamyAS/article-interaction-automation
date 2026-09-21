<script lang="ts">
	import { portalToBody } from '$lib/actions/portal';

	type AppToastTone = 'success' | 'error' | 'loading';
	type AppToastPlacement =
		| 'top-right'
		| 'bottom-right'
		| 'board-bottom-center'
		| 'board-bottom-left'
		| 'board-top-center';

	let {
		tone = 'success',
		text,
		durationMs,
		placement = 'top-right',
		resetKey = text,
		onDismiss
	}: {
		tone?: AppToastTone;
		text: string;
		/** Time on screen in milliseconds. Use 0 for a persistent toast. */
		durationMs: number;
		placement?: AppToastPlacement;
		/** Change this value to restart an otherwise identical toast. */
		resetKey?: unknown;
		onDismiss?: () => void;
	} = $props();

	let visible = $state(true);

	$effect(() => {
		text;
		durationMs;
		resetKey;
		visible = true;
		if (durationMs <= 0) return;

		const timer = setTimeout(() => {
			visible = false;
			onDismiss?.();
		}, durationMs);

		return () => clearTimeout(timer);
	});
</script>

{#if visible}
	<div
		use:portalToBody={placement === 'top-right' || placement === 'bottom-right'}
		class={`app-toast ${placement}`}
		class:success={tone === 'success'}
		class:error={tone === 'error'}
		class:loading={tone === 'loading'}
		role={tone === 'error' ? 'alert' : 'status'}
		aria-live={tone === 'error' ? 'assertive' : 'polite'}
		aria-busy={tone === 'loading'}
	>
		{#if tone === 'loading'}<span class="progress-spinner" aria-hidden="true"></span>{/if}
		<span>{text}</span>
		{#if tone !== 'loading'}<button type="button" class="toast-dismiss" aria-label="Закрыть уведомление" onclick={() => { visible = false; onDismiss?.(); }}>×</button>{/if}
	</div>
{/if}

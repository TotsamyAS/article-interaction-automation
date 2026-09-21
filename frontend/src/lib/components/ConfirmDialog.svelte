<script lang="ts">
  let { title, message, confirmLabel, danger = false, onconfirm, oncancel }: {
    title: string;
    message: string;
    confirmLabel: string;
    danger?: boolean;
    onconfirm: () => void;
    oncancel: () => void;
  } = $props();
  let dialog: HTMLDialogElement;

  $effect(() => { dialog?.showModal(); });
</script>

<dialog bind:this={dialog} oncancel={(event) => { event.preventDefault(); oncancel(); }}>
  <h2>{title}</h2>
  <p>{message}</p>
  <div class="actions">
    <button type="button" class="secondary" onclick={oncancel}>Отмена</button>
    <button type="button" class:danger-action={danger} onclick={onconfirm}>{confirmLabel}</button>
  </div>
</dialog>

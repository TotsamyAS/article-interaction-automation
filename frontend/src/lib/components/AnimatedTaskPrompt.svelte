<script lang="ts">
  import { onDestroy, onMount } from 'svelte';

  let { text }: { text: string } = $props();

  let host: HTMLDivElement;
  let canvas: HTMLCanvasElement;
  let observer: ResizeObserver | null = null;
  let frame = 0;
  let startedAt = 0;
  let reducedMotion = false;

  const FONT_SIZE = 21.6;
  const LINE_HEIGHT = 31;
  const PADDING_Y = 5;
  const WORD_STAGGER_MS = 34;
  const WORD_PULSE_MS = 360;

  type PositionedWord = { text: string; x: number; y: number; index: number };

  function clamp(value: number, min = 0, max = 1) {
    return Math.min(max, Math.max(min, value));
  }

  function layout(context: CanvasRenderingContext2D, width: number) {
    const words = text.trim().split(/\s+/).filter(Boolean);
    const spaceWidth = context.measureText(' ').width;
    const positioned: PositionedWord[] = [];
    let x = 0;
    let y = PADDING_Y;
    let index = 0;

    for (const word of words) {
      const wordWidth = context.measureText(word).width;
      if (x > 0 && x + wordWidth > width) {
        x = 0;
        y += LINE_HEIGHT;
      }
      positioned.push({ text: word, x, y, index });
      x += wordWidth + spaceWidth;
      index += 1;
    }

    return { positioned, height: Math.max(LINE_HEIGHT + PADDING_Y * 2, y + LINE_HEIGHT + PADDING_Y) };
  }

  function draw(now: number) {
    frame = 0;
    if (!host || !canvas) return;

    const width = Math.max(1, Math.floor(host.clientWidth));
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const context = canvas.getContext('2d');
    if (!context) return;

    context.font = `700 ${FONT_SIZE}px Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
    const { positioned, height } = layout(context, width);

    const pixelWidth = Math.max(1, Math.round(width * dpr));
    const pixelHeight = Math.max(1, Math.round(height * dpr));
    if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
      canvas.width = pixelWidth;
      canvas.height = pixelHeight;
      canvas.style.height = `${height}px`;
    }

    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, width, height);
    context.font = `700 ${FONT_SIZE}px Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
    context.textBaseline = 'top';
    context.fillStyle = '#172033';

    const elapsed = reducedMotion ? Number.POSITIVE_INFINITY : now - startedAt;
    for (const word of positioned) {
      const phase = clamp((elapsed - word.index * WORD_STAGGER_MS) / WORD_PULSE_MS);
      const pulse = reducedMotion || phase >= 1 ? 0 : Math.sin(Math.PI * phase);
      context.globalAlpha = 0.9 + pulse * 0.1;
      context.fillText(word.text, word.x, word.y - pulse * 1.25);
    }
    context.globalAlpha = 1;

    const animationEnd = positioned.length * WORD_STAGGER_MS + WORD_PULSE_MS;
    if (!reducedMotion && elapsed < animationEnd) frame = requestAnimationFrame(draw);
  }

  function render() {
    if (frame) cancelAnimationFrame(frame);
    frame = requestAnimationFrame(draw);
  }

  function restartAnimation() {
    startedAt = performance.now();
    render();
  }

  function blockCopy(event: ClipboardEvent) {
    event.preventDefault();
  }

  onMount(() => {
    reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    startedAt = performance.now();
    observer = new ResizeObserver(render);
    observer.observe(host);
    render();
  });

  $effect(() => {
    text;
    if (canvas) restartAnimation();
  });

  onDestroy(() => {
    observer?.disconnect();
    if (frame) cancelAnimationFrame(frame);
  });
</script>

<div class="animated-task-text" bind:this={host} oncopy={blockCopy}>
  <canvas bind:this={canvas} role="img" aria-label={text}></canvas>
  <p class="copy-note">Сформулируйте решение своими словами — текст задания нельзя выделить и скопировать.</p>
</div>

<style>
  .animated-task-text {
    width: min(980px, 100%);
    user-select: none;
    -webkit-user-select: none;
  }

  canvas {
    display: block;
    width: 100%;
    max-width: 100%;
  }

  .copy-note {
    margin: 7px 0 0;
    color: #7a879e;
    font-size: .78rem;
    font-weight: 600;
    line-height: 1.35;
  }
</style>

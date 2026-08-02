<script setup lang="ts">
import { computed, ref } from 'vue';

import { createImageViewerState } from './image-viewer-state.ts';

const props = defineProps<{ images: string[]; initialIndex?: number }>();
const emit = defineEmits<{ close: [] }>();
const state = ref(createImageViewerState(props.images, props.initialIndex || 0));
const transform = computed(() => `translate(${state.value.offset.x}px, ${state.value.offset.y}px) scale(${state.value.scale})`);
let pointerStart: { x: number; y: number } | undefined;

function keydown(event: KeyboardEvent) {
  if (event.key === 'Escape') emit('close');
  else if (event.key === 'ArrowRight') state.value.next();
  else if (event.key === 'ArrowLeft') state.value.previous();
  else if (event.key === '+') state.value.zoomBy(.25);
  else if (event.key === '-') state.value.zoomBy(-.25);
}

function pointerDown(event: PointerEvent) {
  pointerStart = { x: event.clientX, y: event.clientY };
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
}

function pointerMove(event: PointerEvent) {
  if (!pointerStart || state.value.scale <= 1) return;
  const next = { x: event.clientX, y: event.clientY };
  state.value.dragBy(next.x - pointerStart.x, next.y - pointerStart.y);
  pointerStart = next;
}

function pointerUp(event: PointerEvent) {
  if (!pointerStart) return;
  state.value.swipe(event.clientX - pointerStart.x, event.clientY - pointerStart.y);
  pointerStart = undefined;
}
</script>

<template>
  <div class="v2-image-viewer" role="dialog" aria-modal="true" aria-label="图片查看器" tabindex="-1" @keydown="keydown">
    <div class="v2-image-viewer__toolbar">
      <button type="button" aria-label="关闭图片" @click="emit('close')">关闭</button>
      <button type="button" aria-label="上一张" @click="state.previous">上一张</button>
      <span>{{ state.index + 1 }} / {{ state.count }}</span>
      <button type="button" aria-label="下一张" @click="state.next">下一张</button>
      <button type="button" aria-label="缩小" @click="state.zoomBy(-.25)">−</button>
      <button type="button" aria-label="放大" @click="state.zoomBy(.25)">＋</button>
    </div>
    <div class="v2-image-viewer__canvas" @pointerdown="pointerDown" @pointermove="pointerMove" @pointerup="pointerUp">
      <img v-if="state.current" :src="state.current" alt="邮件图片" :style="{ transform }" draggable="false" />
    </div>
  </div>
</template>

<template>
  <div class="dialog-overlay account-icon-crop-overlay" @click.self="requestClose">
    <section
      ref="dialogRef"
      class="dialog account-icon-crop-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="account-icon-crop-title"
      tabindex="-1"
      @keydown="handleDialogKeydown"
    >
      <header class="account-icon-crop-header">
        <div>
          <h3 id="account-icon-crop-title" class="dialog-title">裁剪邮箱图标</h3>
          <p class="dialog-desc">拖动图片调整位置，滚轮、双指或滑杆调整缩放。</p>
        </div>
      </header>

      <div class="account-icon-crop-content">
        <div
          ref="viewportRef"
          class="account-icon-crop-viewport"
          @pointerdown="handlePointerDown"
          @pointermove="handlePointerMove"
          @pointerup="handlePointerUp"
          @pointercancel="handlePointerUp"
          @wheel.prevent="handleWheel"
        >
          <img
            ref="imageRef"
            :src="src"
            alt="待裁剪邮箱图标"
            draggable="false"
            :style="imageStyle"
          />
          <span class="account-icon-crop-frame" aria-hidden="true"></span>
        </div>

        <div class="account-icon-crop-controls">
          <label for="account-icon-scale">缩放</label>
          <input
            id="account-icon-scale"
            v-model.number="state.scale"
            type="range"
            :min="minimumScale"
            :max="maximumScale"
            :step="Math.max(minimumScale / 100, 0.001)"
            @input="normalizeState"
          />
        </div>

        <aside class="account-icon-crop-previews" aria-label="图标效果预览">
          <div>
            <span class="account-icon-crop-preview account-icon-crop-preview--32">
              <img :src="src" alt="" :style="previewStyle(32)" />
            </span>
            <small>32px</small>
          </div>
          <div>
            <span class="account-icon-crop-preview account-icon-crop-preview--48">
              <img :src="src" alt="" :style="previewStyle(48)" />
            </span>
            <small>48px</small>
          </div>
        </aside>
      </div>

      <footer class="dialog-actions">
        <UiButton variant="secondary" :disabled="busy" @click="$emit('reselect')">重新选择</UiButton>
        <UiButton variant="secondary" :disabled="busy" @click="requestClose">取消</UiButton>
        <UiButton variant="primary" :loading="busy" @click="confirmCrop">使用此图标</UiButton>
      </footer>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue';
import UiButton from '../ui/UiButton.vue';
import {
  clampCropState,
  coverScale,
  pinchScale,
  renderAccountIconBlob,
  type CropState,
} from '../../utils/account-icon-crop';

const props = withDefaults(defineProps<{
  src: string;
  naturalWidth: number;
  naturalHeight: number;
  busy?: boolean;
}>(), {
  busy: false,
});

const emit = defineEmits<{
  close: [];
  reselect: [];
  confirm: [blob: Blob];
}>();

const dialogRef = ref<HTMLElement | null>(null);
const viewportRef = ref<HTMLElement | null>(null);
const imageRef = ref<HTMLImageElement | null>(null);
const viewportSize = ref(320);
const minimumScale = computed(() => coverScale(props.naturalWidth, props.naturalHeight, viewportSize.value));
const maximumScale = computed(() => minimumScale.value * 5);
const state = reactive<CropState>({ scale: minimumScale.value, offsetX: 0, offsetY: 0 });
const pointers = new Map<number, { x: number; y: number }>();
let gestureStart: { scale: number; offsetX: number; offsetY: number; distance: number; midpointX: number; midpointY: number } | null = null;

const imageStyle = computed(() => ({
  width: `${props.naturalWidth}px`,
  height: `${props.naturalHeight}px`,
  transform: `translate(calc(-50% + ${state.offsetX}px), calc(-50% + ${state.offsetY}px)) scale(${state.scale})`,
}));

function previewStyle(size: number) {
  const ratio = size / viewportSize.value;
  return {
    width: `${props.naturalWidth * ratio}px`,
    height: `${props.naturalHeight * ratio}px`,
    transform: `translate(calc(-50% + ${state.offsetX * ratio}px), calc(-50% + ${state.offsetY * ratio}px)) scale(${state.scale})`,
  };
}

function normalizeState() {
  Object.assign(state, clampCropState(state, props.naturalWidth, props.naturalHeight, viewportSize.value));
}

function pointerValues() {
  return [...pointers.values()];
}

function pointerDistance(values: { x: number; y: number }[]) {
  return Math.hypot(values[1].x - values[0].x, values[1].y - values[0].y);
}

function pointerMidpoint(values: { x: number; y: number }[]) {
  return { x: (values[0].x + values[1].x) / 2, y: (values[0].y + values[1].y) / 2 };
}

function beginGesture() {
  const values = pointerValues();
  const midpoint = values.length >= 2 ? pointerMidpoint(values) : values[0];
  gestureStart = {
    scale: state.scale,
    offsetX: state.offsetX,
    offsetY: state.offsetY,
    distance: values.length >= 2 ? pointerDistance(values) : 0,
    midpointX: midpoint?.x || 0,
    midpointY: midpoint?.y || 0,
  };
}

function handlePointerDown(event: PointerEvent) {
  viewportRef.value?.setPointerCapture(event.pointerId);
  pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
  beginGesture();
}

function handlePointerMove(event: PointerEvent) {
  if (!pointers.has(event.pointerId) || !gestureStart) return;
  pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
  const values = pointerValues();
  if (values.length >= 2) {
    const midpoint = pointerMidpoint(values);
    state.scale = pinchScale(
      gestureStart.scale,
      gestureStart.distance,
      pointerDistance(values),
      minimumScale.value,
      maximumScale.value,
    );
    state.offsetX = gestureStart.offsetX + midpoint.x - gestureStart.midpointX;
    state.offsetY = gestureStart.offsetY + midpoint.y - gestureStart.midpointY;
  } else {
    state.offsetX = gestureStart.offsetX + values[0].x - gestureStart.midpointX;
    state.offsetY = gestureStart.offsetY + values[0].y - gestureStart.midpointY;
  }
  normalizeState();
}

function handlePointerUp(event: PointerEvent) {
  pointers.delete(event.pointerId);
  if (viewportRef.value?.hasPointerCapture(event.pointerId)) viewportRef.value.releasePointerCapture(event.pointerId);
  if (pointers.size) beginGesture();
  else gestureStart = null;
}

function handleWheel(event: WheelEvent) {
  const factor = event.deltaY < 0 ? 1.08 : 1 / 1.08;
  state.scale = Math.min(maximumScale.value, Math.max(minimumScale.value, state.scale * factor));
  normalizeState();
}

function requestClose() {
  if (!props.busy) emit('close');
}

async function confirmCrop() {
  if (!imageRef.value || props.busy) return;
  const blob = await renderAccountIconBlob(imageRef.value, state, viewportSize.value);
  emit('confirm', blob);
}

function handleDialogKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault();
    requestClose();
    return;
  }
  if (event.key !== 'Tab' || !dialogRef.value) return;
  const focusable = Array.from(dialogRef.value.querySelectorAll<HTMLElement>(
    'button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
  ));
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

let resizeObserver: ResizeObserver | null = null;

function measureViewport() {
  const measured = viewportRef.value?.clientWidth || 320;
  if (measured === viewportSize.value) return;
  viewportSize.value = measured;
  normalizeState();
}

onMounted(async () => {
  await nextTick();
  measureViewport();
  if (typeof ResizeObserver !== 'undefined' && viewportRef.value) {
    resizeObserver = new ResizeObserver(measureViewport);
    resizeObserver.observe(viewportRef.value);
  }
  normalizeState();
  dialogRef.value?.focus();
});

onBeforeUnmount(() => resizeObserver?.disconnect());
</script>

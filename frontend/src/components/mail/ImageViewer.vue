<template>
  <Teleport to="body">
    <Transition name="image-viewer">
      <div v-if="open && currentImage" class="image-viewer" role="dialog" aria-modal="true" aria-label="邮件图片查看器" @click.self="closeViewer">
        <header class="viewer-toolbar">
          <span class="viewer-counter">{{ currentIndex + 1 }} / {{ images.length }}</span>
          <div class="viewer-controls">
            <button type="button" title="缩小" aria-label="缩小图片" @click="zoomOut">−</button>
            <button type="button" title="恢复原始大小" aria-label="恢复原始大小" @click="resetTransform">{{ Math.round(scale * 100) }}%</button>
            <button type="button" title="放大" aria-label="放大图片" @click="zoomIn">＋</button>
            <button type="button" title="关闭" aria-label="关闭图片查看器" @click="closeViewer">×</button>
          </div>
        </header>

        <button v-if="images.length > 1" class="viewer-nav previous" type="button" aria-label="上一张图片" @click="showPrevious">‹</button>
        <button v-if="images.length > 1" class="viewer-nav next" type="button" aria-label="下一张图片" @click="showNext">›</button>

        <div
          ref="stage"
          class="viewer-stage"
          @wheel.prevent="handleWheel"
          @pointerdown="handlePointerDown"
          @pointermove="handlePointerMove"
          @pointerup="handlePointerUp"
          @pointercancel="handlePointerUp"
          @dblclick="toggleZoom"
        >
          <img
            :key="currentImage.src"
            :src="currentImage.src"
            :alt="currentImage.alt || '邮件图片'"
            draggable="false"
            :style="imageStyle"
          />
        </div>
        <div class="viewer-hint">双击或双指缩放 · 拖动查看细节 · 左右滑动切换</div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { clampScale, nextImageIndex, shouldChangeImageFromSwipe } from '../../utils/image-viewer';

export interface ViewerImage {
  src: string;
  alt?: string;
}

const props = withDefaults(defineProps<{
  open: boolean;
  images: ViewerImage[];
  initialIndex?: number;
}>(), {
  initialIndex: 0,
});

const emit = defineEmits<{ close: [] }>();
const stage = ref<HTMLElement | null>(null);
const currentIndex = ref(0);
const scale = ref(1);
const offsetX = ref(0);
const offsetY = ref(0);
const swipeX = ref(0);
const pointers = reactive(new Map<number, { x: number; y: number }>());
let gestureStartX = 0;
let gestureStartY = 0;
let gestureStartTime = 0;
let startOffsetX = 0;
let startOffsetY = 0;
let pinchStartDistance = 0;
let pinchStartScale = 1;

const currentImage = computed(() => props.images[currentIndex.value] || null);
const imageStyle = computed(() => ({
  transform: `translate3d(${offsetX.value + (scale.value === 1 ? swipeX.value * 0.18 : 0)}px, ${offsetY.value}px, 0) scale(${scale.value})`,
  cursor: scale.value > 1 ? 'grab' : 'zoom-in',
}));

function normalizeIndex(index: number) {
  if (!props.images.length) return 0;
  return Math.min(props.images.length - 1, Math.max(0, index));
}

function resetTransform() {
  scale.value = 1;
  offsetX.value = 0;
  offsetY.value = 0;
  swipeX.value = 0;
}

function setScale(nextScale: number) {
  scale.value = clampScale(nextScale);
  if (scale.value === 1) {
    offsetX.value = 0;
    offsetY.value = 0;
  }
}

function zoomIn() {
  setScale(scale.value + 0.5);
}

function zoomOut() {
  setScale(scale.value - 0.5);
}

function toggleZoom() {
  setScale(scale.value > 1 ? 1 : 2.5);
}

function changeImage(direction: -1 | 1) {
  currentIndex.value = nextImageIndex(currentIndex.value, direction, props.images.length);
  resetTransform();
}

function showPrevious() {
  changeImage(-1);
}

function showNext() {
  changeImage(1);
}

function closeViewer() {
  emit('close');
}

function handleWheel(event: WheelEvent) {
  setScale(scale.value + (event.deltaY < 0 ? 0.3 : -0.3));
}

function distanceBetweenPointers() {
  const values = Array.from(pointers.values());
  if (values.length < 2) return 0;
  return Math.hypot(values[0].x - values[1].x, values[0].y - values[1].y);
}

function handlePointerDown(event: PointerEvent) {
  stage.value?.setPointerCapture(event.pointerId);
  pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
  if (pointers.size === 1) {
    gestureStartX = event.clientX;
    gestureStartY = event.clientY;
    gestureStartTime = performance.now();
    startOffsetX = offsetX.value;
    startOffsetY = offsetY.value;
  } else if (pointers.size === 2) {
    pinchStartDistance = distanceBetweenPointers();
    pinchStartScale = scale.value;
    swipeX.value = 0;
  }
}

function handlePointerMove(event: PointerEvent) {
  if (!pointers.has(event.pointerId)) return;
  pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
  if (pointers.size >= 2) {
    const distance = distanceBetweenPointers();
    if (pinchStartDistance > 0) setScale(pinchStartScale * distance / pinchStartDistance);
    return;
  }

  const dx = event.clientX - gestureStartX;
  const dy = event.clientY - gestureStartY;
  if (scale.value > 1) {
    offsetX.value = startOffsetX + dx;
    offsetY.value = startOffsetY + dy;
  } else {
    swipeX.value = dx;
  }
}

function handlePointerUp(event: PointerEvent) {
  const point = pointers.get(event.pointerId);
  pointers.delete(event.pointerId);
  if (pointers.size > 0 || !point) return;

  if (scale.value === 1) {
    const dx = point.x - gestureStartX;
    const dy = point.y - gestureStartY;
    const duration = performance.now() - gestureStartTime;
    if (props.images.length > 1 && shouldChangeImageFromSwipe(dx, dy, duration)) {
      dx < 0 ? showNext() : showPrevious();
    }
    swipeX.value = 0;
  }
  pinchStartDistance = 0;
}

function handleKeydown(event: KeyboardEvent) {
  if (!props.open) return;
  if (event.key === 'Escape') closeViewer();
  else if (event.key === 'ArrowLeft') showPrevious();
  else if (event.key === 'ArrowRight') showNext();
  else if (event.key === '+' || event.key === '=') zoomIn();
  else if (event.key === '-') zoomOut();
  else if (event.key === '0') resetTransform();
}

watch(() => props.open, (open) => {
  if (open) {
    currentIndex.value = normalizeIndex(props.initialIndex);
    resetTransform();
  }
  document.documentElement.classList.toggle('image-viewer-open', open);
}, { immediate: true });

watch(() => props.initialIndex, (index) => {
  if (props.open) currentIndex.value = normalizeIndex(index);
});

onMounted(() => window.addEventListener('keydown', handleKeydown));
onUnmounted(() => {
  window.removeEventListener('keydown', handleKeydown);
  document.documentElement.classList.remove('image-viewer-open');
});
</script>

<style scoped>
.image-viewer {
  position: fixed;
  inset: 0;
  z-index: 12000;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  background: var(--ui-media-viewer-bg);
  color: var(--ui-text-inverse);
  touch-action: none;
  user-select: none;
  -webkit-user-select: none;
  overscroll-behavior: contain;
}
.viewer-toolbar {
  z-index: 3;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: max(12px, env(safe-area-inset-top)) max(14px, env(safe-area-inset-right)) 10px max(14px, env(safe-area-inset-left));
  background: linear-gradient(to bottom, color-mix(in srgb, var(--ui-scrim) 92%, transparent), transparent);
}
.viewer-counter { font-size: 13px; font-variant-numeric: tabular-nums; opacity: .84; }
.viewer-controls { display: flex; align-items: center; gap: 6px; }
.viewer-controls button,
.viewer-nav {
  border: 1px solid color-mix(in srgb, var(--ui-text-inverse) 16%, transparent);
  background: color-mix(in srgb, var(--ui-text-inverse) 10%, transparent);
  color: var(--ui-text-inverse);
  backdrop-filter: blur(14px);
  cursor: pointer;
}
.viewer-controls button { min-width: 40px; height: 40px; border-radius: 12px; font-size: 18px; }
.viewer-controls button:nth-child(2) { min-width: 62px; font-size: 12px; }
.viewer-stage {
  min-width: 0;
  min-height: 0;
  display: grid;
  place-items: center;
  overflow: hidden;
  touch-action: none;
}
.viewer-stage img {
  max-width: min(92vw, 1440px);
  max-height: 82vh;
  object-fit: contain;
  transform-origin: center;
  will-change: transform;
  transition: transform 120ms ease-out;
  -webkit-user-drag: none;
}
.viewer-stage:active img { cursor: grabbing !important; transition: none; }
.viewer-nav {
  position: absolute;
  top: 50%;
  z-index: 4;
  width: 48px;
  height: 64px;
  border-radius: 16px;
  transform: translateY(-50%);
  font-size: 38px;
  line-height: 1;
}
.viewer-nav.previous { left: 16px; }
.viewer-nav.next { right: 16px; }
.viewer-hint { padding: 10px max(14px, env(safe-area-inset-right)) max(14px, env(safe-area-inset-bottom)); text-align: center; font-size: 12px; opacity: .62; }
.image-viewer-enter-active,
.image-viewer-leave-active { transition: opacity 160ms ease; }
.image-viewer-enter-from,
.image-viewer-leave-to { opacity: 0; }
@media (max-width: 640px) {
  .viewer-toolbar { align-items: flex-start; }
  .viewer-controls { gap: 4px; }
  .viewer-controls button { min-width: 36px; height: 36px; border-radius: 10px; }
  .viewer-nav { width: 42px; height: 54px; opacity: .82; }
  .viewer-nav.previous { left: 8px; }
  .viewer-nav.next { right: 8px; }
  .viewer-stage img { max-width: 100vw; max-height: 78vh; }
  .viewer-hint { font-size: 11px; }
}
@media (prefers-reduced-motion: reduce) {
  .viewer-stage img,
  .image-viewer-enter-active,
  .image-viewer-leave-active { transition: none; }
}
</style>

<style>
html.image-viewer-open,
html.image-viewer-open body { overflow: hidden !important; }
</style>

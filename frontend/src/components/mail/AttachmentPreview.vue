<template>
  <Teleport to="body">
    <transition name="attachment-preview-fade">
      <div
        v-if="open && url && kind"
        class="attachment-preview-overlay"
        role="dialog"
        aria-modal="true"
        :aria-label="`预览附件 ${title || '未命名附件'}`"
        @click.self="emit('close')"
      >
        <section class="attachment-preview-panel">
          <header class="attachment-preview-header">
            <div class="attachment-preview-title" :title="title">{{ title || '未命名附件' }}</div>
            <button class="attachment-preview-close" type="button" aria-label="关闭附件预览" @click="emit('close')">
              <span aria-hidden="true">×</span>
            </button>
          </header>
          <div class="attachment-preview-body">
            <img v-if="kind === 'image'" class="attachment-preview-image" :src="url" :alt="title || '附件图片'" />
            <iframe
              v-else-if="kind === 'pdf' || kind === 'text'"
              class="attachment-preview-frame"
              :src="url"
              :title="title || '附件预览'"
            ></iframe>
            <audio v-else-if="kind === 'audio'" class="attachment-preview-audio" :src="url" controls preload="metadata"></audio>
            <video v-else-if="kind === 'video'" class="attachment-preview-video" :src="url" controls preload="metadata"></video>
          </div>
        </section>
      </div>
    </transition>
  </Teleport>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue';
import type { AttachmentPreviewKind } from '../../utils/attachment-preview';

const props = defineProps<{
  open: boolean;
  title: string;
  kind: AttachmentPreviewKind | null;
  url: string;
}>();

const emit = defineEmits<{
  close: [];
}>();

function handleKeydown(event: KeyboardEvent) {
  if (props.open && event.key === 'Escape') emit('close');
}

onMounted(() => window.addEventListener('keydown', handleKeydown));
onBeforeUnmount(() => window.removeEventListener('keydown', handleKeydown));
</script>

<style scoped>
.attachment-preview-overlay {
  position: fixed;
  inset: 0;
  z-index: 1200;
  display: grid;
  place-items: center;
  padding: var(--space-5);
  background: var(--ui-scrim);
}

.attachment-preview-panel {
  width: min(1080px, 94vw);
  max-height: 90vh;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  background: var(--bg-primary);
  box-shadow: var(--shadow-lg);
}

.attachment-preview-header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-height: 52px;
  padding: 0 var(--space-4);
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.attachment-preview-title {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.attachment-preview-close {
  width: 36px;
  height: 36px;
  flex: 0 0 36px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  font: inherit;
  font-size: 24px;
  line-height: 1;
}

.attachment-preview-close:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.attachment-preview-close:focus-visible {
  outline: 2px solid var(--accent-blue);
  outline-offset: 2px;
}

.attachment-preview-body {
  min-height: min(560px, 72vh);
  min-width: 0;
  display: grid;
  place-items: center;
  overflow: auto;
  background: var(--bg-secondary);
}

.attachment-preview-image {
  display: block;
  max-width: 100%;
  max-height: 78vh;
  object-fit: contain;
}

.attachment-preview-frame {
  width: 100%;
  height: min(78vh, 820px);
  border: 0;
  background: var(--bg-primary);
}

.attachment-preview-audio {
  width: min(680px, calc(100% - 32px));
}

.attachment-preview-video {
  display: block;
  max-width: 100%;
  max-height: 78vh;
}

.attachment-preview-fade-enter-active,
.attachment-preview-fade-leave-active {
  transition: opacity var(--motion-fast, 160ms) ease;
}

.attachment-preview-fade-enter-from,
.attachment-preview-fade-leave-to {
  opacity: 0;
}

@media (max-width: 768px) {
  .attachment-preview-overlay {
    padding: 0;
  }

  .attachment-preview-panel {
    width: 100vw;
    max-height: 100dvh;
    height: 100dvh;
    border: 0;
    border-radius: 0;
  }

  .attachment-preview-close {
    width: var(--touch-target, 44px);
    height: var(--touch-target, 44px);
    flex-basis: var(--touch-target, 44px);
  }

  .attachment-preview-body {
    min-height: 0;
    flex: 1;
  }

  .attachment-preview-frame {
    height: 100%;
  }
}
</style>

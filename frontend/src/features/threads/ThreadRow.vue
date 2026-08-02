<script setup lang="ts">
import { computed } from 'vue';

import type { ThreadProjection } from '../../entities/thread/types.ts';

const props = defineProps<{
  thread: ThreadProjection;
  selected?: boolean;
  focused?: boolean;
}>();
const emit = defineEmits<{
  open: [threadId: string];
  select: [threadId: string];
  longpress: [threadId: string];
}>();

let pressTimer: ReturnType<typeof setTimeout> | undefined;
const statusText = computed(() => [
  props.thread.unread_count ? `${props.thread.unread_count} 封未读` : '已读',
  props.thread.has_attachments ? '含附件' : '无附件',
  props.thread.pending_state ? `待同步：${props.thread.pending_state}` : '已同步',
].join('，'));

function startPress() {
  pressTimer = setTimeout(() => emit('longpress', props.thread.id), 500);
}
function endPress() {
  if (pressTimer) clearTimeout(pressTimer);
  pressTimer = undefined;
}
</script>

<template>
  <article
    class="v2-thread-row"
    :class="{ 'is-unread': thread.unread_count > 0, 'is-selected': selected, 'is-focused': focused }"
    :aria-label="`${thread.subject}，${statusText}`"
    :tabindex="focused ? 0 : -1"
    @click="emit('open', thread.id)"
    @pointerdown="startPress"
    @pointerup="endPress"
    @pointercancel="endPress"
  >
    <button
      type="button"
      class="v2-thread-row__select"
      :aria-label="selected ? `取消选择 ${thread.subject}` : `选择 ${thread.subject}`"
      @click.stop="emit('select', thread.id)"
    >
      {{ selected ? '✓' : '○' }}
    </button>
    <div class="v2-thread-row__content">
      <div class="v2-thread-row__headline">
        <strong>{{ thread.subject || '（无主题）' }}</strong>
        <time>{{ new Date(thread.latest_at * 1000).toLocaleDateString() }}</time>
      </div>
      <p>{{ thread.snippet || '暂无摘要' }}</p>
      <span class="v2-sr-only">{{ statusText }}</span>
    </div>
    <div class="v2-thread-row__indicators" aria-hidden="true">
      <span v-if="thread.is_starred">★</span>
      <span v-if="thread.has_attachments">📎</span>
      <span v-if="thread.pending_state">⟳</span>
      <span v-if="thread.unread_count" class="v2-unread-count">{{ thread.unread_count }}</span>
    </div>
  </article>
</template>

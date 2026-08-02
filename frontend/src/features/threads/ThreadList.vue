<script setup lang="ts">
import { nextTick, ref, watch } from 'vue';

import type { ThreadProjection } from '../../entities/thread/types.ts';
import ThreadRow from './ThreadRow.vue';
import { createThreadSelection, shouldHandleListShortcut } from './thread-selection.ts';

const props = defineProps<{
  threads: ThreadProjection[];
  loading?: boolean;
  nextCursor?: string | null;
}>();
const emit = defineEmits<{
  open: [threadId: string];
  loadMore: [];
  selectionChange: [threadIds: string[]];
}>();
const selection = ref(createThreadSelection(props.threads.map((thread) => thread.id)));
watch(() => props.threads.map((thread) => thread.id), (ids) => selection.value.replace(ids));

function toggleSelection(threadId: string) {
  selection.value.toggle(threadId);
  emit('selectionChange', selection.value.selectedIds);
}

function enterMobileSelection(threadId: string) {
  selection.value.enterMobileSelection(threadId);
  emit('selectionChange', selection.value.selectedIds);
}

async function focusCurrent() {
  await nextTick();
  const id = selection.value.focusedId;
  if (id) document.querySelector<HTMLElement>(`[data-thread-id="${CSS.escape(id)}"]`)?.focus();
}

function keydown(event: KeyboardEvent) {
  if (!shouldHandleListShortcut(event.target as HTMLElement | null, event.key)) return;
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault();
    selection.value.move(event.key === 'ArrowDown' ? 1 : -1);
    void focusCurrent();
  } else if (event.key === 'Enter' && selection.value.focusedId) {
    event.preventDefault();
    emit('open', selection.value.focusedId);
  }
}
</script>

<template>
  <section class="v2-thread-list" aria-label="会话列表" @keydown="keydown">
    <ThreadRow
      v-for="thread in threads"
      :key="thread.id"
      :data-thread-id="thread.id"
      :thread="thread"
      :selected="selection.selectedIds.includes(thread.id)"
      :focused="selection.focusedId === thread.id"
      @open="emit('open', $event)"
      @select="toggleSelection"
      @longpress="enterMobileSelection"
    />
    <p v-if="!threads.length && !loading" class="v2-empty-state">这里还没有会话。</p>
    <button v-if="nextCursor" type="button" class="v2-load-more" :disabled="loading" @click="emit('loadMore')">
      {{ loading ? '正在加载…' : '加载更多' }}
    </button>
  </section>
</template>

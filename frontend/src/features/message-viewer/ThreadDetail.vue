<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue';

import type { ThreadDetailResponse } from '../../entities/message/types.ts';
import { apiClient } from '../../shared/api/client.ts';
import { normalizeApiError } from '../../shared/api/errors.ts';
import ImageViewer from './ImageViewer.vue';
import MessageTimelineItem from './MessageTimelineItem.vue';

const props = defineProps<{ threadId: string }>();
const state = reactive<{ loading: boolean; detail?: ThreadDetailResponse; error?: string }>({ loading: false });
const expandedIds = ref(new Set<string>());
const imageSources = ref<string[]>([]);
const imageViewerOpen = ref(false);
const latestUnread = computed(() => {
  const messages = state.detail?.messages || [];
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (!messages[index].is_read) return messages[index].id;
  }
  return messages.length ? messages[messages.length - 1].id : undefined;
});

async function load() {
  state.loading = true;
  state.error = undefined;
  try {
    state.detail = await apiClient.request<ThreadDetailResponse>({
      method: 'GET',
      path: `/api/v2/threads/${encodeURIComponent(props.threadId)}`,
    });
    expandedIds.value = new Set(latestUnread.value ? [latestUnread.value] : []);
  } catch (value: unknown) {
    state.error = normalizeApiError(value).message;
  } finally {
    state.loading = false;
  }
}

function toggle(messageId: string) {
  const next = new Set(expandedIds.value);
  if (next.has(messageId)) next.delete(messageId);
  else next.add(messageId);
  expandedIds.value = next;
}

function openImage(src: string) {
  imageSources.value = [src];
  imageViewerOpen.value = true;
}

watch(() => props.threadId, () => { void load(); });
onMounted(() => { void load(); });
</script>

<template>
  <section class="v2-thread-detail" aria-label="会话详情">
    <header class="v2-thread-detail__header">
      <h1>{{ state.detail?.subject || '会话详情' }}</h1>
      <span v-if="state.detail">{{ state.detail.messages.length }} 封邮件</span>
    </header>
    <p v-if="state.loading" role="status">正在加载会话结构…</p>
    <p v-else-if="state.error" class="v2-error" role="alert">{{ state.error }} <button type="button" @click="load">重试</button></p>
    <div v-else-if="state.detail" class="v2-message-timeline">
      <MessageTimelineItem
        v-for="message in state.detail.messages"
        :key="message.id"
        :message="message"
        :data-body-state="message.body_state"
        :expanded="expandedIds.has(message.id)"
        @toggle="toggle(message.id)"
        @open-image="openImage"
      />
    </div>
    <ImageViewer v-if="imageViewerOpen" :images="imageSources" @close="imageViewerOpen = false" />
  </section>
</template>

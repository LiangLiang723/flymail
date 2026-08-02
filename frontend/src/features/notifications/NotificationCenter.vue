<script setup lang="ts">
import { defineAsyncComponent, onMounted, reactive, ref } from 'vue';

import { apiClient } from '../../shared/api/client.ts';
import type { NotificationSummary } from '../../shared/api/generated.ts';
import { normalizeApiError } from '../../shared/api/errors.ts';

const NotificationDetail = defineAsyncComponent(() => import('./NotificationDetail.vue'));
const state = reactive<{ items: NotificationSummary[]; unread: number; error?: string }>({ items: [], unread: 0 });
const selected = ref<NotificationSummary>();
async function load() {
  try {
    const response = await apiClient.request<{ items: NotificationSummary[]; unread_count: number }>({ method: 'GET', path: '/api/v2/notifications' });
    state.items = response.items; state.unread = response.unread_count;
  } catch (value: unknown) { state.error = normalizeApiError(value).message; }
}
async function markRead(item: NotificationSummary) {
  await apiClient.request({ method: 'POST', path: `/api/v2/notifications/${encodeURIComponent(item.id)}/read` });
  item.read_at = Date.now() / 1000; state.unread = Math.max(0, state.unread - 1);
}
async function dismiss(item: NotificationSummary) {
  await apiClient.request({ method: 'POST', path: `/api/v2/notifications/${encodeURIComponent(item.id)}/dismiss` });
  state.items = state.items.filter((candidate) => candidate.id !== item.id);
}
onMounted(() => { void load(); });
</script>

<template>
  <section class="v2-notification-center" aria-labelledby="notification-title">
    <header><h2 id="notification-title">通知</h2><span>unread 未读 {{ state.unread }}</span></header>
    <p v-if="state.error" class="v2-error">{{ state.error }}</p>
    <article v-for="item in state.items" :key="item.id" :class="{ unread: !item.read_at }">
      <button type="button" @click="selected = item"><strong>{{ item.title }}</strong><span>{{ item.summary }}</span></button>
      <button v-if="!item.read_at" type="button" @click="markRead(item)">标为已读</button>
      <button type="button" @click="dismiss(item)">忽略</button>
    </article>
    <NotificationDetail v-if="selected" :notification="selected" @close="selected = undefined" />
  </section>
</template>

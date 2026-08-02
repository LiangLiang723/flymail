<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { useBootstrap } from '../../app/bootstrap.ts';
import type { ThreadListState } from '../../entities/thread/types.ts';
import { apiClient } from '../../shared/api/client.ts';
import type { ThreadListResponse } from '../../shared/api/generated.ts';
import { normalizeApiError } from '../../shared/api/errors.ts';
import ThreadList from './ThreadList.vue';
import { ThreadListController, createThreadQueryKey, threadCursorMemory } from './thread-query.ts';

const route = useRoute();
const router = useRouter();
const bootstrap = useBootstrap();
const state = reactive<ThreadListState>({ threads: [], nextCursor: null, loading: false, refreshing: false });
const descriptor = computed(() => ({
  scope: String(route.params.scope || 'semantic'),
  key: String(route.params.key || 'inbox'),
  filters: route.query,
}));
const queryKey = computed(() => createThreadQueryKey(bootstrap.state.data?.user.id || 'anonymous', descriptor.value));

const controller = new ThreadListController(async (request, signal) => apiClient.request<ThreadListResponse>({
  method: 'GET',
  path: '/api/v2/threads',
  query: {
    scope: descriptor.value.scope,
    mailbox: descriptor.value.key,
    cursor: typeof request.cursor === 'string' ? request.cursor : undefined,
  },
  signal,
}));

async function load(refresh = false) {
  const key = queryKey.value;
  const cached = threadCursorMemory.get(key);
  if (cached && !refresh) {
    state.threads = cached.threads;
    state.nextCursor = cached.next_cursor || null;
    state.refreshing = true;
  } else {
    state.loading = true;
  }
  state.error = undefined;
  try {
    const response = await controller.load({ key, cursor: null });
    if (key !== queryKey.value) return;
    const next = threadCursorMemory.set(key, response);
    state.threads = next.threads;
    state.nextCursor = next.next_cursor || null;
  } catch (value: unknown) {
    if (value instanceof DOMException && value.name === 'AbortError') return;
    state.error = normalizeApiError(value).message;
  } finally {
    state.loading = false;
    state.refreshing = false;
  }
}

async function loadMore() {
  if (!state.nextCursor || state.loading) return;
  state.loading = true;
  try {
    const response = await apiClient.request<ThreadListResponse>({
      method: 'GET',
      path: '/api/v2/threads',
      query: { scope: descriptor.value.scope, mailbox: descriptor.value.key, cursor: state.nextCursor },
    });
    const next = threadCursorMemory.set(queryKey.value, response, true);
    state.threads = next.threads;
    state.nextCursor = next.next_cursor || null;
  } catch (value: unknown) {
    state.error = normalizeApiError(value).message;
  } finally {
    state.loading = false;
  }
}

function openThread(threadId: string) {
  void router.push({ query: { ...route.query, thread: threadId } });
}

watch(queryKey, () => { void load(); }, { immediate: true });
onBeforeUnmount(() => controller.cancel());
</script>

<template>
  <section class="v2-thread-page">
    <header class="v2-thread-page__header">
      <div>
        <p class="v2-eyebrow">{{ descriptor.scope }}</p>
        <h1>{{ descriptor.key }}</h1>
      </div>
      <button type="button" :disabled="state.refreshing" @click="load(true)">
        {{ state.refreshing ? '刷新中…' : '刷新本地列表' }}
      </button>
    </header>
    <p v-if="state.error" class="v2-error" role="alert">{{ state.error }} <button type="button" @click="load(true)">重试</button></p>
    <ThreadList
      :threads="state.threads"
      :loading="state.loading"
      :next-cursor="state.nextCursor"
      @open="openThread"
      @load-more="loadMore"
    />
  </section>
</template>

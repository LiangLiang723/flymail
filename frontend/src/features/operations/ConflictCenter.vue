<script setup lang="ts">
import { onMounted, reactive } from 'vue';

import { apiClient } from '../../shared/api/client.ts';
import { normalizeApiError } from '../../shared/api/errors.ts';
import { conflictResolutions } from './operation-actions.ts';

interface ConflictItem {
  operation_id: string;
  conflict_type: string;
  summary?: string;
  safe_details?: Record<string, unknown>;
}

const state = reactive<{ loading: boolean; items: ConflictItem[]; error?: string }>({ loading: false, items: [] });

async function load() {
  state.loading = true;
  state.error = undefined;
  try {
    const response = await apiClient.request<{ items: ConflictItem[] }>({ method: 'GET', path: '/api/v2/sync/conflicts' });
    state.items = response.items;
  } catch (value: unknown) {
    state.error = normalizeApiError(value).message;
  } finally {
    state.loading = false;
  }
}

async function resolve(item: ConflictItem, resolution: string) {
  await apiClient.request({
    method: 'POST',
    path: `/api/v2/sync/conflicts/${encodeURIComponent(item.operation_id)}/resolve`,
    body: { resolution },
  });
  state.items = state.items.filter((candidate) => candidate.operation_id !== item.operation_id);
}

onMounted(() => { void load(); });
</script>

<template>
  <section class="v2-conflict-center" aria-labelledby="conflict-title">
    <h2 id="conflict-title">冲突中心</h2>
    <p v-if="state.loading" role="status">正在读取冲突…</p>
    <p v-else-if="state.error" class="v2-error" role="alert">{{ state.error }} <button type="button" @click="load">重试</button></p>
    <p v-else-if="!state.items.length">当前没有需要处理的冲突。</p>
    <article v-for="item in state.items" :key="item.operation_id">
      <h3>{{ item.summary || item.conflict_type }}</h3>
      <dl v-if="item.safe_details">
        <template v-for="(value, key) in item.safe_details" :key="key">
          <dt>{{ key }}</dt><dd>{{ String(value) }}</dd>
        </template>
      </dl>
      <div>
        <button
          v-for="resolution in conflictResolutions(item.conflict_type)"
          :key="resolution"
          type="button"
          @click="resolve(item, resolution)"
        >{{ resolution }}</button>
      </div>
    </article>
  </section>
</template>

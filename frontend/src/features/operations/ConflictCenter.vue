<script setup lang="ts">
import { onMounted, reactive } from 'vue';

import { apiClient } from '../../shared/api/client.ts';
import { normalizeApiError } from '../../shared/api/errors.ts';
import { conflictResolutions } from './operation-actions.ts';

interface ConflictItem {
  operation_id: string;
  operation_type: string;
  target_type: string;
  target_id: string;
  account_id?: string | null;
  status: string;
  error_class: string;
  error_message: string;
  created_at: number;
  updated_at: number;
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

const supportedActions = conflictResolutions('operation_conflict');

async function resolve(item: ConflictItem, action: 'retry_operation' | 'cancel_operation') {
  await apiClient.request({
    method: 'POST',
    path: `/api/v2/sync/conflicts/${encodeURIComponent(item.operation_id)}/resolve`,
    body: { action },
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
      <h3>{{ item.operation_type }} · {{ item.target_type }}</h3>
      <p>{{ item.error_message }}</p>
      <dl>
        <dt>目标</dt><dd>{{ item.target_id }}</dd>
        <dt>账号</dt><dd>{{ item.account_id || '无' }}</dd>
        <dt>状态</dt><dd>{{ item.status }}</dd>
        <dt>错误类别</dt><dd>{{ item.error_class }}</dd>
      </dl>
      <div>
        <button type="button" :data-supported="supportedActions.join(',')" @click="resolve(item, 'retry_operation')">重试操作</button>
        <button type="button" @click="resolve(item, 'cancel_operation')">取消操作</button>
      </div>
    </article>
  </section>
</template>

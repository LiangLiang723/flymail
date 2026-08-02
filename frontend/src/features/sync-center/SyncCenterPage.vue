<script setup lang="ts">
import { onMounted, reactive } from 'vue';

import ConflictCenter from '../operations/ConflictCenter.vue';
import { apiClient } from '../../shared/api/client.ts';
import { normalizeApiError } from '../../shared/api/errors.ts';

interface SyncPhase { completed: number; total: number; updated_at: number }
interface SyncAccount { account_id: string; status: string; idle_status: string; last_activity_at: number; next_reconcile_at: number; failure_count: number; backoff_until: number; phases: Record<string, SyncPhase>; pending_operations: number; conflicts: number }
interface SyncCenterPayload { accounts: SyncAccount[] }
const state = reactive<{ data?: SyncCenterPayload; loading: boolean; error?: string; tasks: Record<string, string> }>({ loading: false, tasks: {} });
async function load() {
  state.loading = true;
  try { state.data = await apiClient.request<SyncCenterPayload>({ method: 'GET', path: '/api/v2/sync' }); }
  catch (value: unknown) { state.error = normalizeApiError(value).message; }
  finally { state.loading = false; }
}
async function refreshAccount(accountId: string) {
  const response = await apiClient.request<{ task_id: string }>({ method: 'POST', path: `/api/v2/sync/accounts/${encodeURIComponent(accountId)}/refresh` });
  state.tasks[accountId] = response.task_id;
}
onMounted(() => { void load(); });
</script>

<template>
  <main class="v2-sync-page">
    <header><div><p class="v2-eyebrow">远端同步</p><h1>同步中心</h1></div><button type="button" @click="load">刷新本地状态</button></header>
    <p>“刷新本地状态”只重新读取本地运行状态；只有每个账号的“手动同步”才会创建远端同步任务。</p>
    <p v-if="state.error" class="v2-error">{{ state.error }}</p>
    <article v-for="account in state.data?.accounts || []" :key="account.account_id">
      <header><h2>{{ account.account_id }}</h2><strong>{{ account.status }}</strong></header>
      <p v-if="account.status === 'auth_required'">账号需要重新授权。<router-link :to="{ name: 'settings', query: { account: account.account_id, action: 'reauthorize' } }">重新授权</router-link></p>
      <dl>
        <dt>下次校对</dt><dd>{{ new Date(account.next_reconcile_at * 1000).toLocaleString() }}</dd>
        <dt>待处理操作</dt><dd>{{ account.pending_operations }}</dd>
        <dt>冲突</dt><dd>{{ account.conflicts }}</dd>
      </dl>
      <ul aria-label="同步阶段">
        <li v-for="(phase, name) in account.phases" :key="name">
          <strong>{{ name === 'summary' ? '摘要 summary' : name === 'body' ? '正文 body' : name === 'index' ? '索引 index' : name }}</strong>：{{ phase.completed }}/{{ phase.total }}
        </li>
      </ul>
      <button type="button" @click="refreshAccount(account.account_id)">手动同步</button>
      <p v-if="state.tasks[account.account_id]" role="status">任务 {{ state.tasks[account.account_id] }} 已创建</p>
    </article>
    <ConflictCenter />
  </main>
</template>

<style scoped>.v2-sync-page{display:grid;gap:var(--v2-space-4);padding:var(--v2-space-4)}.v2-sync-page>header,article>header{display:flex;justify-content:space-between;gap:var(--v2-space-3)}article{padding:var(--v2-space-4);border:1px solid var(--v2-border);border-radius:var(--v2-radius-md)}</style>

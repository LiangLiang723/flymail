<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { useBootstrap } from '../../app/bootstrap.ts';
import { apiClient } from '../../shared/api/client.ts';
import { normalizeApiError } from '../../shared/api/errors.ts';
import { canAccessAdminRoute } from '../admin/admin-state.ts';
import { clearSecretAfter, restoreReviewItems } from './backup-state.ts';

interface Backup { id: string; status: string; archive_name: string; size_bytes: number; created_at: number; last_error_message: string }
const bootstrap = useBootstrap();
const router = useRouter();
const state = reactive<{ items: Backup[]; inspection?: Record<string, unknown>; rehearsal?: Record<string, unknown>; error?: string }>({ items: [] });
const password = ref('');
const selected = ref<Backup>();

async function load() {
  if (!canAccessAdminRoute(bootstrap.state.data?.user.role || '')) { await router.replace('/mail/inbox'); return; }
  const response = await apiClient.request<{ items: Backup[] }>({ method: 'GET', path: '/api/v2/admin/backups' });
  state.items = response.items;
}
async function createBackup() {
  await clearSecretAfter(() => password.value, (value) => { password.value = value; }, (secret) => apiClient.request({ method: 'POST', path: '/api/v2/admin/backups', body: { password: secret } }));
  await load();
}
async function inspect(backup: Backup) {
  selected.value = backup;
  state.inspection = await clearSecretAfter(() => password.value, (value) => { password.value = value; }, (secret) => apiClient.request<Record<string, unknown>>({ method: 'POST', path: `/api/v2/admin/backups/${encodeURIComponent(backup.id)}/inspect`, body: { password: secret } }));
}
async function rehearse() {
  if (!selected.value) return;
  state.rehearsal = await clearSecretAfter(() => password.value, (value) => { password.value = value; }, (secret) => apiClient.request<Record<string, unknown>>({ method: 'POST', path: `/api/v2/admin/backups/${encodeURIComponent(selected.value!.id)}/restore-rehearsal`, body: { password: secret } }));
}
async function safe(action: () => Promise<void>) { try { await action(); } catch (value: unknown) { state.error = normalizeApiError(value).message; password.value = ''; } }
onMounted(() => { void safe(load); });
</script>

<template>
  <main v-if="canAccessAdminRoute(bootstrap.state.data?.user.role || '')" class="v2-backup-page">
    <header><p class="v2-eyebrow">管理员</p><h1>业务备份与恢复演练</h1></header>
    <aside role="note">备份包含明确列出的业务配置和关系，不包含远端缓存、正文缓存、普通附件缓存、会话、日志或可运行后台任务。恢复只做临时数据库和临时对象目录演练，不覆盖生产数据。</aside>
    <label>独立备份密码<input v-model="password" type="password" autocomplete="new-password" /></label>
    <button type="button" :disabled="!password" @click="safe(createBackup)">创建加密备份</button>
    <p v-if="state.error" class="v2-error">{{ state.error }}</p>
    <section><h2>备份列表</h2><article v-for="item in state.items" :key="item.id"><strong>{{ item.archive_name }}</strong><span>{{ item.status }} · {{ item.size_bytes }} 字节</span><button type="button" :disabled="!password" @click="safe(() => inspect(item))">检查范围与计数</button><a :href="`/api/v2/admin/backups/${encodeURIComponent(item.id)}/download`">下载</a></article></section>
    <section v-if="state.inspection"><h2>检查结果（确认演练前）</h2><dl><template v-for="(value, key) in state.inspection" :key="key"><dt>{{ key }}</dt><dd>{{ value }}</dd></template></dl><label>再次输入密码<input v-model="password" type="password" /></label><button type="button" :disabled="!password" @click="safe(rehearse)">执行 restore-rehearsal 隔离恢复演练</button></section>
    <section v-if="state.rehearsal"><h2>演练结果</h2><p>所有未完成发送与远端操作均为 review_required，automatic=false，不会自动执行。</p><ul><li v-for="item in restoreReviewItems({ pending_sends: Number(state.rehearsal.review_required_draft_count || 0), pending_remote_operations: Number(state.rehearsal.review_required_operation_count || 0) })" :key="item.kind">{{ item.kind }}：{{ item.count }} · {{ item.state }}；请重新验证或取消。</li></ul><dl><template v-for="(value, key) in state.rehearsal" :key="key"><dt>{{ key }}</dt><dd>{{ value }}</dd></template></dl></section>
  </main>
</template>

<style scoped>.v2-backup-page{display:grid;gap:var(--v2-space-4);padding:var(--v2-space-4)}aside,section{padding:var(--v2-space-4);border:1px solid var(--v2-border);border-radius:var(--v2-radius-md)}article{display:flex;align-items:center;gap:var(--v2-space-3);padding-block:var(--v2-space-2)}</style>

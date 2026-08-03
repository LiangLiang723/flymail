<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { useBootstrap } from '../../app/bootstrap.ts';
import { apiClient } from '../../shared/api/client.ts';
import { normalizeApiError } from '../../shared/api/errors.ts';
import { canAccessAdminRoute, exactConfirmation } from './admin-state.ts';

interface AdminUser { id: string; username: string; role: 'admin' | 'user'; enabled: boolean; password_version: number }
const bootstrap = useBootstrap();
const router = useRouter();
const state = reactive<{ users: AdminUser[]; error?: string; diagnostics?: Record<string, unknown> }>({ users: [] });
const createForm = reactive({ username: '', password: '', role: 'user' as 'admin' | 'user' });
const actionUser = ref<AdminUser>();
const typed = ref('');
const resetPassword = ref('');

async function load() {
  if (!canAccessAdminRoute(bootstrap.state.data?.user.role || '')) { await router.replace('/mail/inbox'); return; }
  try {
    const [users, diagnostics] = await Promise.all([
      apiClient.request<{ items: AdminUser[] }>({ method: 'GET', path: '/api/v2/admin/users' }),
      apiClient.request<Record<string, unknown>>({ method: 'GET', path: '/api/v2/admin/diagnostics' }),
    ]);
    state.users = users.items;
    state.diagnostics = diagnostics;
  } catch (value: unknown) { state.error = normalizeApiError(value).message; }
}

async function createUser() {
  await apiClient.request({ method: 'POST', path: '/api/v2/admin/users', body: { ...createForm, enabled: true } });
  createForm.username = ''; createForm.password = ''; createForm.role = 'user';
  await load();
}
async function disable(user: AdminUser) {
  if (!exactConfirmation(user.username, typed.value)) return;
  await apiClient.request({ method: 'POST', path: `/api/v2/admin/users/${encodeURIComponent(user.id)}/disable` });
  typed.value = ''; actionUser.value = undefined; await load();
}
async function enable(user: AdminUser) {
  await apiClient.request({ method: 'POST', path: `/api/v2/admin/users/${encodeURIComponent(user.id)}/enable` });
  await load();
}
async function reset(user: AdminUser) {
  if (!exactConfirmation(user.username, typed.value) || !resetPassword.value) return;
  await apiClient.request({ method: 'POST', path: `/api/v2/admin/users/${encodeURIComponent(user.id)}/reset-password`, body: { new_password: resetPassword.value } });
  typed.value = ''; resetPassword.value = ''; actionUser.value = undefined; await load();
}
onMounted(() => { void load(); });
</script>

<template>
  <main v-if="canAccessAdminRoute(bootstrap.state.data?.user.role || '')" class="v2-admin-page">
    <header><p class="v2-eyebrow">管理员</p><h1>用户与运行诊断</h1></header>
    <p v-if="state.error" class="v2-error">{{ state.error }}</p>
    <section><h2>创建用户</h2><form @submit.prevent="createUser"><input v-model="createForm.username" placeholder="用户名" required /><input v-model="createForm.password" type="password" placeholder="初始密码" required /><select v-model="createForm.role"><option value="user">普通用户</option><option value="admin">管理员</option></select><button type="submit">创建</button></form></section>
    <section><h2>用户</h2><article v-for="user in state.users" :key="user.id"><strong>{{ user.username }}</strong><span>{{ user.role }} · {{ user.enabled ? '启用' : '禁用' }}</span><button v-if="user.enabled" type="button" @click="actionUser = user">禁用或重置</button><button v-else type="button" @click="enable(user)">启用</button></article></section>
    <section><h2>安全诊断</h2><dl><template v-for="(value, key) in state.diagnostics" :key="key"><dt>{{ key }}</dt><dd>{{ value }}</dd></template></dl></section>
    <form v-if="actionUser" role="dialog" aria-modal="true" @submit.prevent><h2>高风险操作：{{ actionUser.username }}</h2><p>请输入精确用户名确认。</p><input v-model="typed" /><input v-model="resetPassword" type="password" placeholder="新密码（不能为空）" /><button type="button" :disabled="!exactConfirmation(actionUser.username, typed)" @click="disable(actionUser)">确认 disable 禁用</button><button type="button" :disabled="!exactConfirmation(actionUser.username, typed) || !resetPassword" @click="reset(actionUser)">确认 reset-password</button><button type="button" @click="actionUser = undefined; typed = ''; resetPassword = ''">取消</button></form>
  </main>
</template>

<style scoped>.v2-admin-page{display:grid;gap:var(--v2-space-4);padding:var(--v2-space-4)}section,form[role=dialog]{padding:var(--v2-space-4);border:1px solid var(--v2-border);border-radius:var(--v2-radius-md)}article{display:flex;gap:var(--v2-space-3);align-items:center;padding-block:var(--v2-space-2)}</style>

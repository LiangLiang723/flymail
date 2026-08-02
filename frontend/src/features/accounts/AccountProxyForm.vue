<script setup lang="ts">
import { onMounted, reactive } from 'vue';

import { apiClient } from '../../shared/api/client.ts';
import { normalizeApiError } from '../../shared/api/errors.ts';

const form = reactive({ scheme: 'http' as const, host: '', port: 8080, username: '', password: '', hasCredentials: false, error: '' });
async function load() {
  const proxy = await apiClient.request<{ scheme: 'http'; host: string; port: number; has_credentials: boolean } | null>({ method: 'GET', path: '/api/v2/accounts/proxy' });
  if (proxy) { form.scheme = proxy.scheme; form.host = proxy.host; form.port = proxy.port; form.hasCredentials = proxy.has_credentials; }
}
async function save() {
  form.error = '';
  try {
    const response = await apiClient.request<{ has_credentials: boolean }>({ method: 'PUT', path: '/api/v2/accounts/proxy', body: { scheme: form.scheme, host: form.host, port: form.port, username: form.username, password: form.password } });
    form.hasCredentials = response.has_credentials; form.password = '';
  } catch (value: unknown) { form.error = normalizeApiError(value).message; }
}
onMounted(() => { void load().catch((value) => { form.error = normalizeApiError(value).message; }); });
</script>

<template>
  <form class="v2-account-proxy" @submit.prevent="save">
    <h2>账号流量代理</h2>
    <p>此代理仅用于第三方邮箱账号流量与 OAuth 请求，不代理内部 FlyMail API、MySQL 或容器通信。</p>
    <label>协议<select v-model="form.scheme"><option value="http">HTTP CONNECT</option></select></label>
    <label>主机<input v-model.trim="form.host" autocomplete="off" /></label>
    <label>端口<input v-model.number="form.port" type="number" min="1" max="65535" /></label>
    <label>用户名<input v-model.trim="form.username" autocomplete="off" /></label>
    <label>密码<input v-model="form.password" type="password" autocomplete="new-password" :placeholder="form.hasCredentials ? '已配置；留空保持不变' : '未配置'" /></label>
    <button type="submit">保存代理</button><p v-if="form.error" class="v2-error">{{ form.error }}</p>
  </form>
</template>

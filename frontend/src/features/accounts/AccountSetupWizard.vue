<script setup lang="ts">
import { reactive, ref } from 'vue';

import { apiClient } from '../../shared/api/client.ts';
import { normalizeApiError } from '../../shared/api/errors.ts';
import { rememberOAuthStart } from './oauth-state.ts';

const emit = defineEmits<{ created: []; close: [] }>();
const mode = ref<'choose' | 'oauth' | 'generic'>('choose');
const form = reactive({ provider_key: 'gmail', email: '', display_name: '', credential: '', imap_host: '', imap_port: 993, smtp_host: '', smtp_port: 465, error: '' });

async function startOAuth() {
  form.error = '';
  try {
    const response = await apiClient.request<{ state: string; authorization_url: string; expires_at: number }>({
      method: 'POST', path: '/api/v2/accounts/oauth/start',
      body: { provider_key: form.provider_key, email: form.email, display_name: form.display_name, redirect_uri: `${window.location.origin}/oauth/callback` },
    });
    rememberOAuthStart(response.state, response.expires_at * 1000);
    window.location.assign(response.authorization_url);
  } catch (value: unknown) { form.error = normalizeApiError(value).message; }
}

async function createGeneric() {
  form.error = '';
  try {
    await apiClient.request({ method: 'POST', path: '/api/v2/accounts', body: {
      provider_key: 'generic', email: form.email, display_name: form.display_name,
      credential_type: 'password', credential: form.credential,
      endpoint_config: {
        imap: { host: form.imap_host, port: form.imap_port, security: 'tls' },
        smtp: { host: form.smtp_host, port: form.smtp_port, security: 'tls' },
      },
      poll_interval_seconds: 300,
    } });
    form.credential = '';
    emit('created');
  } catch (value: unknown) { form.error = normalizeApiError(value).message; form.credential = ''; }
}
</script>

<template>
  <section class="v2-account-wizard" role="dialog" aria-modal="true" aria-labelledby="account-wizard-title">
    <h2 id="account-wizard-title">添加邮箱账号</h2>
    <template v-if="mode === 'choose'">
      <button type="button" @click="mode = 'oauth'; form.provider_key = 'gmail'">Google OAuth</button>
      <button type="button" @click="mode = 'oauth'; form.provider_key = 'outlook'">Microsoft OAuth</button>
      <button type="button" @click="mode = 'generic'">generic 通用 IMAP / SMTP</button>
    </template>
    <form v-else-if="mode === 'oauth'" @submit.prevent="startOAuth">
      <p>OAuth 授权将在服务商页面完成；浏览器只保留当前内存 state，取消、过期或不匹配都会拒绝回调。</p>
      <label>邮箱<input v-model.trim="form.email" type="email" required /></label><label>显示名称<input v-model.trim="form.display_name" /></label>
      <button type="submit">前往授权</button><button type="button" @click="mode = 'choose'">返回</button>
    </form>
    <form v-else @submit.prevent="createGeneric">
      <label>邮箱<input v-model.trim="form.email" type="email" required /></label><label>显示名称<input v-model.trim="form.display_name" /></label>
      <label>授权码或密码<input v-model="form.credential" type="password" autocomplete="new-password" required /></label>
      <label>IMAP 主机<input v-model.trim="form.imap_host" required /></label><label>IMAP 端口<input v-model.number="form.imap_port" type="number" min="1" max="65535" /></label>
      <label>SMTP 主机<input v-model.trim="form.smtp_host" required /></label><label>SMTP 端口<input v-model.number="form.smtp_port" type="number" min="1" max="65535" /></label>
      <button type="submit">创建并验证</button><button type="button" @click="mode = 'choose'">返回</button>
    </form>
    <p v-if="form.error" class="v2-error">{{ form.error }}</p><button type="button" @click="emit('close')">关闭</button>
  </section>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue';

import { useBootstrap } from '../../app/bootstrap.ts';
import { apiClient } from '../../shared/api/client.ts';
import { normalizeApiError } from '../../shared/api/errors.ts';
import AccountIconEditor from '../account-customization/AccountIconEditor.vue';
import AccountProxyForm from './AccountProxyForm.vue';
import AccountSetupWizard from './AccountSetupWizard.vue';

interface AccountDetail {
  id: string;
  provider_key: string;
  email: string;
  display_name: string;
  status: string;
  poll_interval_seconds: number;
}
interface Identity {
  id: string;
  account_id: string;
  from_address: string;
  display_name: string;
  reply_to: string;
  signature_html: string;
  signature_text: string;
  is_default: boolean;
  is_verified: boolean;
}

const bootstrap = useBootstrap();
const showWizard = ref(false);
const editingIcon = ref('');
const selectedId = ref('');
const state = reactive<{ detail?: AccountDetail; identities: Identity[]; error: string; task: string }>({ identities: [], error: '', task: '' });
const credential = reactive({ credential_type: 'password' as 'password' | 'authorization_code', credential: '' });
const identityForm = reactive({ id: '', display_name: '', reply_to: '', signature_html: '', signature_text: '', is_default: false });
const accounts = computed(() => bootstrap.state.data?.accounts || []);

async function reload() {
  showWizard.value = false;
  editingIcon.value = '';
  await bootstrap.load(true);
  if (selectedId.value) await selectAccount(selectedId.value);
}
async function selectAccount(accountId: string) {
  selectedId.value = accountId;
  state.error = '';
  try {
    const [detail, identities] = await Promise.all([
      apiClient.request<AccountDetail>({ method: 'GET', path: `/api/v2/accounts/${encodeURIComponent(accountId)}` }),
      apiClient.request<{ items: Identity[] }>({ method: 'GET', path: `/api/v2/accounts/${encodeURIComponent(accountId)}/identities` }),
    ]);
    state.detail = detail;
    state.identities = identities.items;
    const identity = identities.items.find((item) => item.is_default) || identities.items[0];
    if (identity) editIdentity(identity);
  } catch (value: unknown) { state.error = normalizeApiError(value).message; }
}
async function setEnabled(enabled: boolean) {
  if (!state.detail) return;
  state.detail = await apiClient.request<AccountDetail>({ method: 'PATCH', path: `/api/v2/accounts/${encodeURIComponent(state.detail.id)}`, body: { enabled } });
  await bootstrap.load(true);
}
async function verifyAccount() {
  if (!state.detail) return;
  const response = await apiClient.request<{ job_id: string }>({ method: 'POST', path: `/api/v2/accounts/${encodeURIComponent(state.detail.id)}/verify` });
  state.task = `账号校验已排队：${response.job_id}`;
}
async function updateCredential() {
  if (!state.detail || !credential.credential) return;
  try {
    const response = await apiClient.request<{ job_id: string }>({ method: 'PUT', path: `/api/v2/accounts/${encodeURIComponent(state.detail.id)}/credentials`, body: { credential_type: credential.credential_type, credential: credential.credential } });
    state.task = `重新授权已排队：${response.job_id}`;
  } finally { credential.credential = ''; }
}
async function deleteAccount() {
  if (!state.detail) return;
  const confirmEmail = window.prompt(`输入 ${state.detail.email} 确认删除账号及其本地邮件数据`);
  if (confirmEmail !== state.detail.email) { state.error = '确认邮箱不匹配，未执行删除'; return; }
  const response = await apiClient.request<{ cleanup_job_id: string }>({ method: 'DELETE', path: `/api/v2/accounts/${encodeURIComponent(state.detail.id)}`, body: { confirm_email: confirmEmail } });
  state.task = `删除任务已排队：${response.cleanup_job_id}`;
  selectedId.value = ''; state.detail = undefined; state.identities = [];
  await bootstrap.load(true);
}
function editIdentity(item: Identity) {
  Object.assign(identityForm, {
    id: item.id, display_name: item.display_name, reply_to: item.reply_to,
    signature_html: item.signature_html, signature_text: item.signature_text,
    is_default: item.is_default,
  });
}
function selectIdentity(event: Event) {
  const identityId = (event.target as HTMLSelectElement).value;
  const identity = state.identities.find((item) => item.id === identityId);
  if (identity) editIdentity(identity);
}
async function saveIdentity() {
  if (!state.detail || !identityForm.id) return;
  const updated = await apiClient.request<Identity>({
    method: 'PATCH',
    path: `/api/v2/accounts/${encodeURIComponent(state.detail.id)}/identities/${encodeURIComponent(identityForm.id)}`,
    body: {
      display_name: identityForm.display_name,
      reply_to: identityForm.reply_to,
      signature_html: identityForm.signature_html,
      signature_text: identityForm.signature_text,
      is_default: identityForm.is_default,
    },
  });
  state.identities = state.identities.map((item) => item.id === updated.id ? updated : item);
  state.task = '发件身份与签名已保存';
}
</script>

<template>
  <main class="v2-accounts-page">
    <header>
      <div><p class="v2-eyebrow">邮箱账号</p><h1>账号与连接</h1></div>
      <button type="button" @click="showWizard = true">添加账号</button>
    </header>
    <p v-if="state.error" class="v2-error" role="alert">{{ state.error }}</p>
    <p v-if="state.task" role="status">{{ state.task }}</p>

    <section aria-labelledby="account-list-title">
      <h2 id="account-list-title">已连接账号</h2>
      <article v-for="account in accounts" :key="account.id" :class="{ selected: selectedId === account.id }">
        <button class="account-summary" type="button" @click="selectAccount(account.id)">
          <strong>{{ account.display_name }}</strong><span>{{ account.email }}</span><small>{{ account.provider_key }} · {{ account.status }}</small>
        </button>
        <div class="v2-account-actions">
          <button type="button" @click="editingIcon = editingIcon === account.id ? '' : account.id">编辑图标</button>
        </div>
        <AccountIconEditor v-if="editingIcon === account.id" :account-id="account.id" :provider="account.provider_key" @updated="reload" />
      </article>
      <p v-if="!accounts.length">尚未添加邮箱账号。</p>
    </section>

    <section v-if="state.detail" class="account-detail" aria-labelledby="account-detail-title">
      <h2 id="account-detail-title">{{ state.detail.email }}</h2>
      <div class="action-row">
        <button type="button" @click="setEnabled(state.detail.status === 'disabled')">{{ state.detail.status === 'disabled' ? '启用账号' : '禁用账号' }}</button>
        <button type="button" @click="verifyAccount">重新校验连接</button>
        <button class="danger" type="button" @click="deleteAccount">删除账号</button>
      </div>
      <form @submit.prevent="updateCredential">
        <h3>重新授权</h3>
        <label>凭证类型<select v-model="credential.credential_type"><option value="password">密码或授权码</option><option value="authorization_code">授权代码</option></select></label>
        <label>新凭证<input v-model="credential.credential" type="password" autocomplete="new-password" required /></label>
        <button type="submit">更新凭证并验证</button>
      </form>
      <form v-if="identityForm.id" @submit.prevent="saveIdentity">
        <h3>发件身份与签名</h3>
        <label>身份<select :value="identityForm.id" @change="selectIdentity"><option v-for="item in state.identities" :key="item.id" :value="item.id">{{ item.from_address }}</option></select></label>
        <label>显示名称<input v-model.trim="identityForm.display_name" /></label>
        <label>回复地址<input v-model.trim="identityForm.reply_to" type="email" /></label>
        <label>HTML 签名<textarea v-model="identityForm.signature_html" rows="5" /></label>
        <label>纯文本签名<textarea v-model="identityForm.signature_text" rows="3" /></label>
        <label><input v-model="identityForm.is_default" type="checkbox" />设为默认身份</label>
        <button type="submit">保存身份签名</button>
      </form>
    </section>

    <AccountProxyForm />
    <AccountSetupWizard v-if="showWizard" @created="reload" @close="showWizard = false" />
  </main>
</template>

<style scoped>
.v2-accounts-page{display:grid;gap:var(--v2-space-4);padding:var(--v2-space-4)}
.v2-accounts-page>header,.v2-accounts-page article,.action-row{display:flex;align-items:center;justify-content:space-between;gap:var(--v2-space-3)}
section,.v2-account-proxy{padding:var(--v2-space-4);border:1px solid var(--v2-border);border-radius:var(--v2-radius-md)}
article{padding-block:var(--v2-space-3);border-bottom:1px solid var(--v2-border);flex-wrap:wrap}.account-summary{display:grid;text-align:left;background:none;border:0}.selected{background:var(--v2-surface-muted)}
.v2-account-actions,.action-row{display:flex;align-items:center;gap:var(--v2-space-2)}.account-detail,form{display:grid;gap:var(--v2-space-3)}label{display:grid;gap:var(--v2-space-1)}.danger{color:var(--v2-danger)}
</style>

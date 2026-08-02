<script setup lang="ts">
import { onMounted, reactive } from 'vue';

import { apiClient } from '../../shared/api/client.ts';
import type { SettingsResponse } from '../../shared/api/generated.ts';
import { normalizeApiError } from '../../shared/api/errors.ts';
import { DEFAULT_BODY_QUOTA_BYTES, formatQuota, quotaDecreaseNeedsCleanup } from './settings-state.ts';

const state = reactive<{ data?: SettingsResponse; loading: boolean; errors: Record<string, string>; saved: Record<string, boolean> }>({ loading: false, errors: {}, saved: {} });

async function load() {
  state.loading = true;
  try { state.data = await apiClient.request<SettingsResponse>({ method: 'GET', path: '/api/v2/settings' }); }
  catch (value: unknown) { state.errors.load = normalizeApiError(value).message; }
  finally { state.loading = false; }
}

async function saveCard(name: string, body: Record<string, unknown>) {
  state.errors[name] = '';
  state.saved[name] = false;
  try {
    state.data = await apiClient.request<SettingsResponse>({ method: 'PUT', path: '/api/v2/settings', body });
    state.saved[name] = true;
  } catch (value: unknown) { state.errors[name] = normalizeApiError(value).message; }
}

function quotaInput(key: 'body_cache_quota_bytes' | 'attachment_cache_quota_bytes', value: string) {
  if (!state.data) return;
  state.data[key] = Math.max(0, Number(value || 0)) * 1024 ** 2;
}

onMounted(() => { void load(); });
</script>

<template>
  <main class="v2-settings-page">
    <header><p class="v2-eyebrow">偏好</p><h1>设置</h1></header>
    <nav class="v2-settings-links" aria-label="设置分类">
      <router-link to="/profile">个人资料</router-link>
      <router-link to="/accounts">邮箱账号</router-link>
      <router-link to="/contacts">联系人</router-link>
      <router-link to="/notification-settings">通知与图床</router-link>
      <router-link to="/about">关于</router-link>
    </nav>
    <p v-if="state.loading">正在加载设置…</p>
    <p v-if="state.errors.load" class="v2-error">{{ state.errors.load }}</p>
    <template v-if="state.data">
      <section>
        <h2>外观</h2>
        <label>主题<select v-model="state.data.ui_preferences.theme"><option value="system">跟随系统</option><option value="light">浅色</option><option value="dark">深色</option></select></label>
        <label>密度<select v-model="state.data.ui_preferences.density"><option value="comfortable">舒适</option><option value="compact">紧凑</option></select></label>
        <button type="button" @click="saveCard('appearance', { ui_preferences: state.data.ui_preferences })">保存外观</button>
        <p v-if="state.errors.appearance" class="v2-error">{{ state.errors.appearance }}</p>
      </section>
      <section>
        <h2>缓存配额</h2>
        <p>正文默认建议：5 GB（{{ formatQuota(DEFAULT_BODY_QUOTA_BYTES) }}）；输入 0 表示不限额。</p>
        <label>正文缓存 MB<input :value="Math.round(state.data.body_cache_quota_bytes / 1024 ** 2)" type="number" min="0" @input="quotaInput('body_cache_quota_bytes', ($event.target as HTMLInputElement).value)" /></label>
        <label>附件缓存 MB<input :value="Math.round(state.data.attachment_cache_quota_bytes / 1024 ** 2)" type="number" min="0" @input="quotaInput('attachment_cache_quota_bytes', ($event.target as HTMLInputElement).value)" /></label>
        <p>逻辑使用：正文 {{ formatQuota(state.data.body_cache_usage_bytes) }}；附件 {{ formatQuota(state.data.attachment_cache_usage_bytes) }}。物理对象仅在无其他引用时释放。</p>
        <p v-if="state.data.cleanup_task_id" role="status">清理任务 cleanup_task_id：{{ state.data.cleanup_task_id }}</p>
        <button type="button" @click="saveCard('quota', { body_cache_quota_bytes: state.data.body_cache_quota_bytes, attachment_cache_quota_bytes: state.data.attachment_cache_quota_bytes })">保存配额</button>
        <p v-if="quotaDecreaseNeedsCleanup(DEFAULT_BODY_QUOTA_BYTES, state.data.body_cache_quota_bytes)">降低配额会排队异步清理任务，不删除邮件元数据。</p>
        <p v-if="state.errors.quota" class="v2-error">{{ state.errors.quota }}</p>
      </section>
      <section>
        <h2>远程图片</h2>
        <label>默认策略<select v-model="state.data.remote_image_policy.default"><option value="block">阻止</option><option value="allow">允许</option></select></label>
        <button type="button" @click="saveCard('images', { remote_image_policy: state.data.remote_image_policy })">保存图片策略</button>
      </section>
      <section>
        <h2>写信</h2>
        <label>自动保存秒数<input v-model.number="state.data.compose_preferences.autosave_seconds" type="number" min="3" max="300" /></label>
        <button type="button" @click="saveCard('compose', { compose_preferences: state.data.compose_preferences })">保存写信设置</button>
      </section>
    </template>
  </main>
</template>

<style scoped>.v2-settings-page{display:grid;gap:var(--v2-space-4);padding:var(--v2-space-4)}section{display:grid;gap:var(--v2-space-3);padding:var(--v2-space-4);border:1px solid var(--v2-border);border-radius:var(--v2-radius-md);background:var(--v2-surface)}</style>

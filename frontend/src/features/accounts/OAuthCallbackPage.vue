<script setup lang="ts">
import { onMounted, reactive } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { apiClient } from '../../shared/api/client.ts';
import { normalizeApiError } from '../../shared/api/errors.ts';
import { forgetOAuthStart, readOAuthStart, validateOAuthCallback } from './oauth-state.ts';

const route = useRoute();
const router = useRouter();
const state = reactive({ status: 'checking', message: '正在验证 OAuth 回调…' });

onMounted(async () => {
  const returnedState = typeof route.query.state === 'string' ? route.query.state : '';
  const pending = readOAuthStart(returnedState);
  const code = typeof route.query.code === 'string' ? route.query.code : '';
  const callbackStatus = route.query.error ? 'cancelled' : code ? 'success' : 'failed';
  const validation = validateOAuthCallback({ expectedState: returnedState, returnedState, expiresAt: pending?.expiresAt || 0, status: callbackStatus });
  if (!validation.ok) {
    state.status = 'error'; state.message = `OAuth 回调已拒绝：${validation.reason}`;
    return;
  }
  try {
    await apiClient.request({ method: 'GET', path: '/api/v2/accounts/oauth/callback', query: { state: returnedState, code } });
    forgetOAuthStart(returnedState);
    state.status = 'success'; state.message = '账号授权成功，后台同步任务已创建。';
    setTimeout(() => { void router.replace('/settings'); }, 700);
  } catch (value: unknown) {
    state.status = 'error'; state.message = normalizeApiError(value).message;
  }
});
</script>

<template><main class="v2-oauth-callback"><h1>邮箱授权</h1><p :class="{ 'v2-error': state.status === 'error' }" role="status">{{ state.message }}</p><router-link to="/settings">返回设置</router-link></main></template>

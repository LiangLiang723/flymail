<script setup lang="ts">
import { onMounted, reactive } from 'vue';

import { apiClient } from '../../shared/api/client.ts';
import { OPENAPI_SHA256, OPENAPI_VERSION } from '../../shared/api/generated.ts';
import { normalizeApiError } from '../../shared/api/errors.ts';

const state = reactive<{ version?: string; schemaVersion?: number; error?: string }>({});
onMounted(async () => {
  try {
    const response = await apiClient.request<{ version: string; schema_version: number }>({ method: 'GET', path: '/api/v2/version' });
    state.version = response.version;
    state.schemaVersion = response.schema_version;
  } catch (value: unknown) { state.error = normalizeApiError(value).message; }
});
</script>

<template>
  <main class="v2-about-page">
    <p class="v2-eyebrow">FlyMail</p><h1>关于</h1>
    <p v-if="state.error" class="v2-error">{{ state.error }}</p>
    <dl><dt>后端版本</dt><dd>{{ state.version || '读取中…' }}</dd><dt>数据库模式版本</dt><dd>{{ state.schemaVersion ?? '读取中…' }}</dd><dt>前端契约版本</dt><dd>{{ OPENAPI_VERSION }}</dd><dt>契约指纹</dt><dd>{{ OPENAPI_SHA256 }}</dd><dt>产品</dt><dd>FlyMail V2 多用户邮件客户端</dd></dl>
    <p v-if="state.version && state.version !== OPENAPI_VERSION" role="status">后端版本与前端冻结契约不同，请完成安全刷新或部署匹配版本。</p>
    <section><h2>许可证与文档</h2><p>许可证信息随源代码仓库发布；部署、数据目录、隐私和恢复边界请参阅项目文档。</p><a href="/README.md" target="_blank" rel="noopener noreferrer">部署文档</a></section>
  </main>
</template>

<style scoped>.v2-about-page{max-width:760px;padding:var(--v2-space-5)}dl{display:grid;grid-template-columns:max-content 1fr;gap:var(--v2-space-2) var(--v2-space-4)}dd{overflow-wrap:anywhere}</style>

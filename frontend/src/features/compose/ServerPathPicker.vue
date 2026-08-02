<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';

import { apiClient } from '../../shared/api/client.ts';
import { normalizeApiError } from '../../shared/api/errors.ts';
import type { DraftAttachment } from './compose-state.ts';

interface StorageRoot { id: string; label: string; visibility_scope: string }
interface StorageEntry { name: string; relative_path: string; entry_type: 'file' | 'directory'; size_bytes: number }

const props = defineProps<{ draftId: string }>();
const emit = defineEmits<{ imported: [attachment: DraftAttachment]; close: [] }>();
const state = reactive<{ roots: StorageRoot[]; items: StorageEntry[]; path: string; error?: string; loading: boolean }>({ roots: [], items: [], path: '', loading: false });
const rootId = ref('');

async function loadRoots() {
  state.loading = true;
  try {
    const response = await apiClient.request<{ items: StorageRoot[] }>({ method: 'GET', path: '/api/v2/storage/roots' });
    state.roots = response.items;
    if (!rootId.value && state.roots[0]) rootId.value = state.roots[0].id;
    if (rootId.value) await browse('');
  } catch (value: unknown) { state.error = normalizeApiError(value).message; }
  finally { state.loading = false; }
}

async function browse(relativePath: string) {
  if (!rootId.value) return;
  state.loading = true;
  state.error = undefined;
  try {
    const response = await apiClient.request<{ root_id: string; path: string; items: StorageEntry[] }>({
      method: 'GET', path: `/api/v2/storage/roots/${encodeURIComponent(rootId.value)}/browse`, query: { path: relativePath },
    });
    state.path = response.path;
    state.items = response.items;
  } catch (value: unknown) { state.error = normalizeApiError(value).message; }
  finally { state.loading = false; }
}

async function choose(item: StorageEntry) {
  if (item.entry_type === 'directory') return browse(item.relative_path);
  const attachment = await apiClient.request<DraftAttachment>({
    method: 'POST', path: `/api/v2/drafts/${encodeURIComponent(props.draftId)}/attachments/import`,
    body: { root_id: rootId.value, relative_path: item.relative_path },
  });
  emit('imported', attachment);
}

function parentPath() {
  const parts = state.path.split('/').filter(Boolean);
  parts.pop();
  void browse(parts.join('/'));
}

onMounted(() => { void loadRoots(); });
</script>

<template>
  <section class="v2-server-path-picker" role="dialog" aria-modal="true" aria-labelledby="server-picker-title">
    <h2 id="server-picker-title">从授权存储添加附件</h2>
    <label>授权根目录<select v-model="rootId" @change="browse('')"><option v-for="root in state.roots" :key="root.id" :value="root.id">{{ root.label }}</option></select></label>
    <p>当前位置：{{ state.path || '根目录' }}</p>
    <button v-if="state.path" type="button" @click="parentPath">上一级</button>
    <p v-if="state.error" class="v2-error" role="alert">{{ state.error }}</p>
    <ul aria-label="授权目录内容">
      <li v-for="item in state.items" :key="item.relative_path">
        <button type="button" @click="choose(item)">{{ item.entry_type === 'directory' ? '文件夹' : '文件' }}：{{ item.name }}</button>
      </li>
    </ul>
    <button type="button" @click="emit('close')">关闭</button>
  </section>
</template>

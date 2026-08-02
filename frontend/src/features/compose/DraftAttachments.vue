<script setup lang="ts">
import { reactive } from 'vue';

import { apiClient, getCsrfToken } from '../../shared/api/client.ts';
import type { DraftAttachment } from './compose-state.ts';

const props = defineProps<{ draftId: string; attachments: DraftAttachment[]; maxBytes?: number }>();
const emit = defineEmits<{
  uploaded: [attachment: DraftAttachment];
  removed: [attachmentId: string];
}>();
const uploads = reactive<Record<string, { progress: number; error?: string; xhr?: XMLHttpRequest }>>({});

function upload(file: File) {
  const maxBytes = props.maxBytes || 100 * 1024 * 1024;
  if (file.size > maxBytes) {
    uploads[file.name] = { progress: 0, error: `附件超过 ${Math.round(maxBytes / 1024 / 1024)} MB 限制` };
    return;
  }
  const xhr = new XMLHttpRequest();
  uploads[file.name] = { progress: 0, xhr };
  xhr.open('POST', `/api/v2/drafts/${encodeURIComponent(props.draftId)}/attachments`);
  xhr.withCredentials = true;
  xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
  xhr.setRequestHeader('X-Filename', file.name);
  const csrf = getCsrfToken();
  if (csrf) xhr.setRequestHeader('X-CSRF-Token', csrf);
  xhr.upload.onprogress = (event) => {
    if (event.lengthComputable) uploads[file.name].progress = Math.round((event.loaded / event.total) * 100);
  };
  xhr.onerror = () => { uploads[file.name] = { progress: 0, error: '上传失败' }; };
  xhr.onabort = () => { uploads[file.name] = { progress: 0, error: '上传已取消' }; };
  xhr.onload = () => {
    if (xhr.status >= 200 && xhr.status < 300) {
      emit('uploaded', JSON.parse(xhr.responseText) as DraftAttachment);
      delete uploads[file.name];
    } else {
      uploads[file.name] = { progress: 0, error: `上传失败（${xhr.status}）` };
    }
  };
  xhr.send(file);
}

function filesSelected(event: Event) {
  for (const file of Array.from((event.target as HTMLInputElement).files || [])) upload(file);
  (event.target as HTMLInputElement).value = '';
}

async function remove(attachmentId: string) {
  await apiClient.request({ method: 'DELETE', path: `/api/v2/drafts/${encodeURIComponent(props.draftId)}/attachments/${encodeURIComponent(attachmentId)}` });
  emit('removed', attachmentId);
}
</script>

<template>
  <section class="v2-draft-attachments" aria-labelledby="draft-attachments-title">
    <h2 id="draft-attachments-title">附件</h2>
    <label class="v2-file-button">从设备添加<input type="file" multiple hidden @change="filesSelected" /></label>
    <ul>
      <li v-for="attachment in attachments" :key="attachment.id">
        <span>{{ attachment.filename }} · {{ attachment.size_bytes }} 字节</span>
        <button type="button" @click="remove(attachment.id)">移除</button>
      </li>
      <li v-for="(item, name) in uploads" :key="name">
        <span>{{ name }} · {{ item.progress }}%</span>
        <button v-if="item.xhr" type="button" @click="item.xhr.abort()">取消</button>
        <span v-if="item.error" class="v2-error">{{ item.error }}</span>
      </li>
    </ul>
  </section>
</template>

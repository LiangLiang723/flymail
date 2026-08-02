<script setup lang="ts">
import { ref } from 'vue';

import { apiClient, getCsrfToken } from '../../shared/api/client.ts';
import { cropImageToBlob } from './image-crop.ts';

const props = defineProps<{ accountId: string; provider: string }>();
const emit = defineEmits<{ updated: [value: unknown] }>();
const mode = ref<'provider' | 'preset' | 'upload'>('provider');
const preset = ref('mail');

async function savePreset() {
  if (mode.value === 'upload') return;
  const response = await apiClient.request({ method: 'PUT', path: `/api/v2/accounts/${encodeURIComponent(props.accountId)}/icon`, body: { mode: mode.value, value: mode.value === 'preset' ? preset.value : '' } });
  emit('updated', response);
}
async function upload(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0];
  if (!file) return;
  const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
  const size = Math.min(bitmap.width, bitmap.height);
  bitmap.close();
  const blob = await cropImageToBlob(file, { x: 0, y: 0, size, width: size, height: size, orientation: 1 }, 256);
  const response = await fetch(`/api/v2/accounts/${encodeURIComponent(props.accountId)}/icon/upload`, { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'image/webp', 'X-CSRF-Token': getCsrfToken() }, body: blob });
  if (!response.ok) throw new Error('图标上传失败');
  mode.value = 'upload';
  emit('updated', await response.json());
}
</script>

<template>
  <section class="v2-account-icon-editor">
    <h3>账号图标</h3>
    <label><input v-model="mode" type="radio" value="provider" /> provider 服务商图标</label>
    <label><input v-model="mode" type="radio" value="preset" /> preset 内置图标</label>
    <select v-if="mode === 'preset'" v-model="preset"><option value="mail">邮件</option><option value="briefcase">工作</option><option value="personal">个人</option><option value="school">学校</option><option value="star">星标</option><option value="cloud">云端</option></select>
    <button v-if="mode !== 'upload'" type="button" @click="savePreset">保存图标</button>
    <label>upload 上传 256×256 WebP<input type="file" accept="image/png,image/jpeg,image/webp" @change="upload" /></label>
  </section>
</template>

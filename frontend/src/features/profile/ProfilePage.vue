<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';

import { apiClient, getCsrfToken } from '../../shared/api/client.ts';
import { normalizeApiError } from '../../shared/api/errors.ts';
import AvatarCropDialog from './AvatarCropDialog.vue';

interface Profile { user_uid: string; username: string; role: string; nickname: string; avatar_url: string | null }
const state = reactive<{ profile?: Profile; error?: string; saving: boolean }>({ saving: false });
const avatarFile = ref<File>();

async function load() { state.profile = await apiClient.request<Profile>({ method: 'GET', path: '/api/v2/profile' }); }
async function saveNickname() {
  if (!state.profile) return;
  state.saving = true;
  try { state.profile = await apiClient.request<Profile>({ method: 'PATCH', path: '/api/v2/profile', body: { nickname: state.profile.nickname } }); }
  catch (value: unknown) { state.error = normalizeApiError(value).message; }
  finally { state.saving = false; }
}
async function uploadAvatar(blob: Blob) {
  const response = await fetch('/api/v2/profile/avatar', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'image/webp', 'X-CSRF-Token': getCsrfToken() }, body: blob });
  if (!response.ok) throw new Error('头像上传失败');
  state.profile = await response.json() as Profile;
  avatarFile.value = undefined;
}
function selectAvatar(event: Event) { avatarFile.value = (event.target as HTMLInputElement).files?.[0]; }
onMounted(() => { void load().catch((value) => { state.error = normalizeApiError(value).message; }); });
</script>

<template>
  <main class="v2-profile-page">
    <p class="v2-eyebrow">个人资料</p><h1>资料与头像</h1>
    <p v-if="state.error" class="v2-error">{{ state.error }}</p>
    <form v-if="state.profile" @submit.prevent="saveNickname">
      <img v-if="state.profile.avatar_url" :src="state.profile.avatar_url" alt="当前头像" width="96" height="96" />
      <label>用户名<input :value="state.profile.username" disabled /></label>
      <label>角色<input :value="state.profile.role" disabled /></label>
      <label>昵称 nickname<input v-model.trim="state.profile.nickname" maxlength="191" /></label>
      <label>更换头像<input type="file" accept="image/png,image/jpeg,image/webp" @change="selectAvatar" /></label>
      <button type="submit" :disabled="state.saving">保存资料</button>
    </form>
    <AvatarCropDialog v-if="avatarFile" :file="avatarFile" @cropped="uploadAvatar" @close="avatarFile = undefined" />
  </main>
</template>

<style scoped>.v2-profile-page{display:grid;gap:var(--v2-space-4);padding:var(--v2-space-4)}form{display:grid;gap:var(--v2-space-3);max-width:560px}</style>

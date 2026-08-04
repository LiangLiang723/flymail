<template>
  <PageFrame template="document" width="form" class="profile-page ui-page">
    <template #header>
      <PageHeader title="个人资料" description="管理登录用户名、显示昵称和头像。" />
    </template>

    <div class="document-column profile-document">
      <UiCard class="profile-card" variant="raised" padding="lg">
        <div class="avatar-section">
          <div class="profile-avatar" aria-hidden="true">
            <img v-if="user?.avatar_url" :src="user.avatar_url" alt="" />
            <span v-else>{{ avatarInitial }}</span>
          </div>
          <div class="avatar-copy">
            <strong>{{ displayName }}</strong>
            <span>支持 JPG、PNG、GIF 或 WebP，上传后会裁剪为 256 × 256。</span>
            <div class="avatar-actions">
              <label class="btn btn-secondary avatar-upload" :class="{ disabled: uploading }">
                <input type="file" accept="image/*" :disabled="uploading" @change="uploadAvatar" />
                {{ uploading ? '上传中…' : '更换头像' }}
              </label>
              <button v-if="user?.avatar_url" class="btn btn-danger" type="button" :disabled="uploading" @click="removeAvatar">
                移除头像
              </button>
            </div>
          </div>
        </div>

        <form class="profile-form" @submit.prevent="saveProfile">
          <UiField class="profile-field" label="用户名" for-id="profile-username" hint="用于登录，至少 3 个字符。">
            <input id="profile-username" v-model.trim="form.username" class="ui-input" autocomplete="username" maxlength="191" />
          </UiField>
          <UiField class="profile-field" label="昵称" for-id="profile-nickname" hint="昵称为空时显示用户名。">
            <input id="profile-nickname" v-model.trim="form.nickname" class="ui-input" maxlength="191" placeholder="在界面中显示的名称" />
          </UiField>
          <div class="profile-actions">
            <UiButton variant="primary" type="submit" :loading="saving">保存资料</UiButton>
          </div>
        </form>
      </UiCard>
    </div>
  </PageFrame>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';
import PageFrame from '../components/layout/PageFrame.vue';
import PageHeader from '../components/layout/PageHeader.vue';
import UiButton from '../components/ui/UiButton.vue';
import UiCard from '../components/ui/UiCard.vue';
import UiField from '../components/ui/UiField.vue';
import { useUIStore } from '../stores/ui';
import api from '../utils/api';

interface ProfileUser {
  id: string;
  uid: string;
  username: string;
  nickname?: string;
  display_name?: string;
  avatar_url?: string;
  role: string;
  status: string;
}

const props = defineProps<{ user: ProfileUser | null }>();
const emit = defineEmits<{ updated: [user: ProfileUser] }>();
const uiStore = useUIStore();
const saving = ref(false);
const uploading = ref(false);
const form = reactive({ username: '', nickname: '' });

const displayName = computed(() => props.user?.display_name || props.user?.nickname || props.user?.username || '用户');
const avatarInitial = computed(() => displayName.value.trim().charAt(0).toUpperCase() || 'U');

watch(() => props.user, (user) => {
  form.username = user?.username || '';
  form.nickname = user?.nickname || '';
}, { immediate: true });

async function saveProfile() {
  if (form.username.length < 3) {
    uiStore.error('用户名至少 3 位');
    return;
  }
  saving.value = true;
  try {
    const data = await api.patch('/auth/profile', {
      username: form.username,
      nickname: form.nickname,
    }) as any;
    emit('updated', data.user);
    uiStore.success('个人资料已更新');
  } catch (error: any) {
    uiStore.error(error?.error || error?.message || '保存资料失败');
  } finally {
    saving.value = false;
  }
}

async function uploadAvatar(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (!file) return;
  uploading.value = true;
  try {
    const body = new FormData();
    body.append('avatar', file);
    const data = await api.post('/auth/avatar', body, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }) as any;
    emit('updated', data.user);
    uiStore.success('头像已更新');
  } catch (error: any) {
    uiStore.error(error?.error || error?.message || '上传头像失败');
  } finally {
    uploading.value = false;
  }
}

async function removeAvatar() {
  uploading.value = true;
  try {
    const data = await api.delete('/auth/avatar') as any;
    emit('updated', data.user);
    uiStore.success('头像已移除');
  } catch (error: any) {
    uiStore.error(error?.error || error?.message || '移除头像失败');
  } finally {
    uploading.value = false;
  }
}
</script>

<style scoped>
.profile-page { width: 100%; min-width: 0; min-height: 0; }
.profile-card {
  width: min(760px, 100%);
  display: grid;
  gap: 22px;
  padding: 22px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-lg);
  background: var(--ui-surface-1);
  box-shadow: none;
}
.avatar-section { display: flex; align-items: center; gap: 20px; }
.profile-avatar {
  width: 72px;
  height: 72px;
  flex: 0 0 72px;
  display: grid;
  place-items: center;
  overflow: hidden;
  border-radius: 50%;
  background: linear-gradient(145deg, var(--ui-accent), var(--ui-accent-secondary));
  color: var(--ui-text-inverse);
  font-size: 30px;
  font-weight: 760;
}
.profile-avatar img { width: 100%; height: 100%; object-fit: cover; }
.avatar-copy { min-width: 0; display: grid; gap: 6px; }
.avatar-copy strong { color: var(--ui-text-1); font-size: 18px; }
.avatar-copy > span, .profile-field small { color: var(--ui-text-3); font-size: 12px; line-height: 1.5; }
.avatar-actions, .profile-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 6px; }
.avatar-upload { position: relative; overflow: hidden; }
.avatar-upload input { position: absolute; inset: 0; opacity: 0; cursor: pointer; }
.avatar-upload.disabled { opacity: .55; pointer-events: none; }
.profile-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.profile-actions { grid-column: 1 / -1; }
.profile-field { display: grid; gap: 7px; color: var(--ui-text-2); font-size: 13px; font-weight: 620; }
.profile-field input {
  width: 100%;
  height: 44px;
  box-sizing: border-box;
  padding: 0 13px;
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius-md);
  background: var(--ui-surface-1);
  color: var(--ui-text-1);
  font: inherit;
}
.profile-field input:focus { outline: 2px solid color-mix(in srgb, var(--ui-accent) 36%, transparent); border-color: var(--ui-accent); }
@media (max-width: 640px) {
  .profile-card { padding: 18px; }
  .avatar-section { align-items: flex-start; }
  .profile-avatar { width: 64px; height: 64px; flex-basis: 64px; }
  .profile-form { grid-template-columns: minmax(0, 1fr); }
}
</style>

<template>
  <div ref="menuRoot" class="app-user-menu">
    <button
      class="sidebar-profile-trigger"
      type="button"
      :aria-expanded="open"
      aria-haspopup="menu"
      title="打开账号菜单"
      @click="open = !open"
    >
      <span class="user-avatar">
        <img v-if="user?.avatar_url" :src="user.avatar_url" alt="" />
        <span v-else>{{ initial }}</span>
      </span>
      <span class="sidebar-profile-copy">
        <strong>{{ displayName }}</strong>
        <small>@{{ username }}</small>
      </span>
    </button>

    <Transition name="popover">
      <div v-if="open" class="user-menu-popover" role="menu">
        <div class="user-menu-summary">
          <span class="user-avatar large">
            <img v-if="user?.avatar_url" :src="user.avatar_url" alt="" />
            <span v-else>{{ initial }}</span>
          </span>
          <span>
            <strong>{{ displayName }}</strong>
            <small>{{ username }} · {{ roleLabel }}</small>
          </span>
        </div>

        <button type="button" role="menuitem" @click="navigate('profile')">
          <AppIcon name="accounts" :size="18" />
          <span>个人资料</span>
        </button>
        <button v-if="user?.role === 'admin'" type="button" role="menuitem" @click="navigate('users')">
          <AppIcon name="users" :size="18" />
          <span>用户管理</span>
        </button>
        <button type="button" role="menuitem" @click="navigate('notifications')">
          <AppIcon name="notifications" :size="18" />
          <span>第三方通知</span>
        </button>
        <button type="button" role="menuitem" @click="navigate('signatures')">
          <AppIcon name="signature" :size="18" />
          <span>签名管理</span>
        </button>
        <button type="button" role="menuitem" @click="navigate('settings')">
          <AppIcon name="settings" :size="18" />
          <span>设置</span>
        </button>

        <div class="user-menu-separator" role="separator"></div>

        <button type="button" role="menuitem" @click="navigate('about')">
          <AppIcon name="info" :size="18" />
          <span>关于</span>
        </button>
        <button type="button" role="menuitem" @click="emitAction('change-password')">
          <AppIcon name="lock" :size="18" />
          <span>修改密码</span>
        </button>
        <button type="button" role="menuitem" class="logout-item" @click="emitAction('logout')">
          <AppIcon name="logout" :size="18" />
          <span>退出登录</span>
        </button>
      </div>
    </Transition>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import AppIcon from '../AppIcon.vue';

interface MenuUser {
  username: string;
  nickname?: string;
  display_name?: string;
  avatar_url?: string;
  role: string;
}

const props = defineProps<{ user: MenuUser | null }>();

const emit = defineEmits<{
  navigate: [key: string];
  'change-password': [];
  logout: [];
}>();

const menuRoot = ref<HTMLElement | null>(null);
const open = ref(false);
const username = computed(() => String(props.user?.username || '用户'));
const displayName = computed(() => String(props.user?.display_name || props.user?.nickname || username.value));
const roleLabel = computed(() => props.user?.role === 'admin' ? '管理员' : '普通用户');
const initial = computed(() => displayName.value.trim().charAt(0).toUpperCase() || 'U');

function navigate(key: string) {
  open.value = false;
  emit('navigate', key);
}

function emitAction(action: 'change-password' | 'logout') {
  open.value = false;
  if (action === 'logout') emit('logout');
  else emit('change-password');
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') open.value = false;
}

function handlePointerDown(event: PointerEvent) {
  if (menuRoot.value && !menuRoot.value.contains(event.target as Node)) open.value = false;
}

onMounted(() => {
  window.addEventListener('keydown', handleKeydown);
  window.addEventListener('pointerdown', handlePointerDown);
});

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeydown);
  window.removeEventListener('pointerdown', handlePointerDown);
});
</script>

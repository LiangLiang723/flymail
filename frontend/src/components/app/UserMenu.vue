<template>
  <div class="app-user-menu">
    <button
      class="sidebar-profile-trigger"
      type="button"
      :aria-expanded="open"
      aria-haspopup="menu"
      title="账号菜单"
      @click="open = !open"
    >
      <span class="user-avatar">{{ initial }}</span>
      <span class="sidebar-profile-copy">
        <strong>{{ username }}</strong>
        <small>{{ roleLabel }}</small>
      </span>
      <AppIcon class="profile-chevron" name="chevron-down" :size="15" />
    </button>

    <Transition name="popover">
      <div v-if="open" class="user-menu-popover" role="menu">
        <div class="user-menu-summary">
          <span class="user-avatar large">{{ initial }}</span>
          <span>
            <strong>{{ username }}</strong>
            <small>{{ roleLabel }}</small>
          </span>
        </div>
        <button type="button" role="menuitem" @click="emitAction('change-password')">
          <AppIcon name="lock" :size="17" />
          <span>修改密码</span>
        </button>
        <button type="button" role="menuitem" class="logout-item" @click="emitAction('logout')">
          <AppIcon name="logout" :size="17" />
          <span>退出登录</span>
        </button>
      </div>
    </Transition>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import AppIcon from '../AppIcon.vue';

const props = defineProps<{
  user: {
    username: string;
    role: string;
  } | null;
}>();

const emit = defineEmits<{
  'change-password': [];
  logout: [];
}>();

const open = ref(false);
const username = computed(() => String(props.user?.username || '用户'));
const roleLabel = computed(() => props.user?.role === 'admin' ? '管理员' : '普通用户');
const initial = computed(() => username.value.trim().charAt(0).toUpperCase() || 'U');

function emitAction(action: 'change-password' | 'logout') {
  open.value = false;
  if (action === 'change-password') emit('change-password');
  else emit('logout');
}

function handleEscape(event: KeyboardEvent) {
  if (event.key === 'Escape') open.value = false;
}

onMounted(() => window.addEventListener('keydown', handleEscape));
onUnmounted(() => window.removeEventListener('keydown', handleEscape));
</script>

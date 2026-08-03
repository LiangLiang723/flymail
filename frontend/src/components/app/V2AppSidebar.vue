<template>
  <button
    v-if="mobile && mobileOpen"
    class="mobile-sidebar-backdrop"
    type="button"
    aria-label="关闭导航"
    @click="emit('close-mobile')"
  ></button>

  <aside
    class="app-sidebar"
    :class="{ 'is-collapsed': collapsed && !mobile, 'is-mobile-open': mobileOpen }"
    :aria-hidden="mobile && !mobileOpen"
  >
    <div class="sidebar-icon-rail" aria-hidden="true"></div>

    <div class="sidebar-header">
      <button
        v-if="collapsed && !mobile"
        class="sidebar-collapsed-toggle"
        type="button"
        aria-label="展开侧边栏"
        title="展开侧边栏"
        @click="emit('toggle-collapse')"
      >
        <img class="sidebar-collapsed-logo" src="/icon.png" alt="" />
        <AppIcon class="sidebar-collapsed-expand" name="panel-left-open" :size="18" />
      </button>

      <template v-else>
        <div class="sidebar-brand">
          <span class="sidebar-brand-logo"><img src="/icon.png" alt="FlyMail" /></span>
          <span class="sidebar-brand-copy">
            <strong>FlyMail</strong>
            <small>V2 邮件工作台</small>
          </span>
        </div>
        <button
          v-if="!mobile"
          class="sidebar-header-action"
          type="button"
          aria-label="折叠侧边栏"
          title="折叠侧边栏"
          @click="emit('toggle-collapse')"
        >
          <AppIcon name="panel-left-close" :size="18" />
        </button>
        <button
          v-else
          class="sidebar-mobile-close"
          type="button"
          aria-label="关闭导航"
          @click="emit('close-mobile')"
        >
          <AppIcon name="close" :size="19" />
        </button>
      </template>
    </div>

    <div class="sidebar-scroll">
      <nav class="nav-list" aria-label="主导航">
        <button
          v-for="item in visibleItems"
          :key="item.key"
          class="sidebar-row nav-item"
          :class="{ active: currentRoute === item.key }"
          :title="collapsed && !mobile ? item.label : undefined"
          type="button"
          @click="navigate(item.key)"
        >
          <span class="sidebar-row-icon"><AppIcon :name="item.icon" :size="19" /></span>
          <span class="sidebar-label-pane nav-item-label">{{ item.label }}</span>
        </button>
      </nav>
    </div>

    <div class="sidebar-bottom">
      <button
        class="sidebar-row sidebar-action notification-button"
        :class="{ active: currentRoute === 'notifications' }"
        type="button"
        title="通知中心"
        @click="navigate('notifications')"
      >
        <span class="sidebar-row-icon">
          <AppIcon name="bell" :size="19" />
          <strong v-if="unreadNotifications" class="notification-count">
            {{ unreadNotifications > 99 ? '99+' : unreadNotifications }}
          </strong>
        </span>
        <span class="sidebar-label-pane">通知中心</span>
      </button>

      <UserMenu
        :user="menuUser"
        @navigate="navigateUserMenu"
        @change-password="emit('change-password')"
        @logout="emit('logout')"
      />

      <div class="sidebar-version sidebar-label-pane">
        <span>FlyMail</span>
        <span>v{{ appVersion }}</span>
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { computed } from 'vue';

import type { UserSummary } from '../../shared/api/generated.ts';
import AppIcon from '../AppIcon.vue';
import UserMenu from './UserMenu.vue';

interface NavigationItem {
  key: string;
  label: string;
  icon: string;
  adminOnly?: boolean;
}

const props = defineProps<{
  collapsed: boolean;
  mobile: boolean;
  mobileOpen: boolean;
  currentRoute: string;
  user: UserSummary | null;
  appVersion: string;
  unreadNotifications: number;
}>();

const emit = defineEmits<{
  'toggle-collapse': [];
  'close-mobile': [];
  navigate: [key: string];
  'change-password': [];
  logout: [];
}>();

const items: NavigationItem[] = [
  { key: 'mail', label: '邮件管理', icon: 'mail' },
  { key: 'compose', label: '写信', icon: 'send' },
  { key: 'search', label: '搜索', icon: 'inbox' },
  { key: 'contacts', label: '联系人', icon: 'contacts' },
  { key: 'accounts', label: '账号管理', icon: 'accounts' },
  { key: 'sync', label: '同步管理', icon: 'sync' },
  { key: 'backup', label: '业务备份', icon: 'backup', adminOnly: true },
];

const visibleItems = computed(() => items.filter((item) => !item.adminOnly || props.user?.role === 'admin'));
const menuUser = computed(() => props.user ? {
  username: props.user.username,
  nickname: props.user.nickname,
  display_name: props.user.display_name,
  avatar_url: props.user.avatar_url || undefined,
  role: props.user.role,
} : null);

function navigate(key: string) {
  emit('navigate', key);
  emit('close-mobile');
}

function navigateUserMenu(key: string) {
  const routeKey = key === 'users'
    ? 'admin'
    : key === 'notifications'
      ? 'notification-settings'
      : key;
  navigate(routeKey);
}
</script>

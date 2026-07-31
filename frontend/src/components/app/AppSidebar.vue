<template>
  <button
    v-if="mobile && mobileOpen"
    class="mobile-sidebar-backdrop"
    type="button"
    aria-label="关闭导航"
    @click="$emit('close-mobile')"
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
        @click="$emit('toggle-collapse')"
      >
        <img class="sidebar-collapsed-logo" src="/icon.png" alt="" />
        <AppIcon class="sidebar-collapsed-expand" name="panel-left-open" :size="18" />
      </button>

      <template v-else>
        <div class="sidebar-brand">
          <span class="sidebar-brand-logo"><img src="/icon.png" alt="FlyMail" /></span>
          <span class="sidebar-brand-copy">
            <strong>FlyMail</strong>
            <small>Docker 多用户版</small>
          </span>
        </div>
        <button
          v-if="!mobile"
          class="sidebar-header-action"
          type="button"
          aria-label="折叠侧边栏"
          title="折叠侧边栏"
          @click="$emit('toggle-collapse')"
        >
          <AppIcon name="panel-left-close" :size="18" />
        </button>
        <button
          v-else
          class="sidebar-mobile-close"
          type="button"
          aria-label="关闭导航"
          @click="$emit('close-mobile')"
        >
          <AppIcon name="close" :size="19" />
        </button>
      </template>
    </div>

    <div class="sidebar-scroll">
      <nav class="nav-list" aria-label="主导航">
        <button
          v-for="item in navItems"
          :key="item.key"
          class="sidebar-row nav-item"
          :class="{ active: currentView === item.key }"
          :title="collapsed && !mobile ? item.label : undefined"
          @click="$emit('navigate', item.key)"
        >
          <span class="sidebar-row-icon"><AppIcon :name="item.icon" :size="19" /></span>
          <span class="sidebar-label-pane nav-item-label">{{ item.label }}</span>
        </button>
      </nav>

      <section v-if="mobile && currentView === 'mail'" class="mobile-mail-navigation">
        <h3>邮箱账号</h3>
        <div v-for="account in mailStore.accounts" :key="account.id" class="mobile-account-row">
          <button
            type="button"
            class="mobile-account-item"
            :class="{ active: mailStore.currentAccountId === account.id }"
            @click="selectMailNavigation({ type: 'account', id: account.id })"
          >
            <AccountIcon :account="account" size="md" decorative />
            <span class="mobile-account-copy">
              <strong>{{ accountDisplayName(account) }}</strong>
              <small>{{ account.email }}</small>
            </span>
          </button>
          <button
            v-if="mailStore.reauthAccountIds.has(account.id)"
            class="mobile-account-reauth"
            type="button"
            title="重新授权"
            aria-label="重新授权"
            @click="selectMailNavigation({ type: 'reauth', id: account.id })"
          >
            <AppIcon name="sync" :size="15" />
          </button>
        </div>

        <h3 class="folder-title">文件夹</h3>
        <button
          v-for="folder in mailStore.folders"
          :key="folder.path"
          type="button"
          class="mobile-folder-item"
          :class="{ active: mailStore.currentFolder === folder.path }"
          @click="selectMailNavigation({ type: 'folder', path: folder.path })"
        >
          <AppIcon :name="folderIconName(folder.name)" :size="17" />
          <span>{{ mailStore.folderDisplayName(folder.name) }}</span>
          <small>{{ mobileFolderCount(folder) }}</small>
        </button>
      </section>
    </div>

    <div class="sidebar-bottom">
      <button class="sidebar-row sidebar-action notification-button" type="button" title="通知中心" @click="$emit('open-notifications')">
        <span class="sidebar-row-icon">
          <AppIcon name="bell" :size="19" />
          <strong v-if="mailStore.unreadNotificationCount" class="notification-count">
            {{ mailStore.unreadNotificationCount > 99 ? '99+' : mailStore.unreadNotificationCount }}
          </strong>
        </span>
        <span class="sidebar-label-pane">通知中心</span>
      </button>

      <UserMenu
        :user="user"
        @navigate="$emit('navigate', $event)"
        @change-password="$emit('change-password')"
        @logout="$emit('logout')"
      />

      <div class="sidebar-version sidebar-label-pane">
        <span>FlyMail</span>
        <span>v{{ appVersion }}</span>
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
import AccountIcon from '../account/AccountIcon.vue';
import AppIcon from '../AppIcon.vue';
import UserMenu from './UserMenu.vue';
import { useMailStore } from '../../stores/mail';

interface NavItem {
  key: string;
  label: string;
  icon: string;
}

type MailNavigation =
  | { type: 'account' | 'reauth'; id: string }
  | { type: 'folder'; path: string };

defineProps<{
  collapsed: boolean;
  mobile: boolean;
  mobileOpen: boolean;
  currentView: string;
  navItems: NavItem[];
  user: {
    username: string;
    nickname?: string;
    display_name?: string;
    avatar_url?: string;
    role: string;
  } | null;
  appVersion: string;
}>();

const emit = defineEmits<{
  'toggle-collapse': [];
  'close-mobile': [];
  navigate: [key: string];
  'open-notifications': [];
  'change-password': [];
  logout: [];
  'mail-navigation': [detail: MailNavigation];
}>();

const mailStore = useMailStore();

function accountDisplayName(account: any) {
  return String(account?.remark || '').trim() || account?.email || '邮箱账号';
}

function folderIconName(name: string) {
  const icons: Record<string, string> = {
    收件箱: 'inbox',
    已发送: 'send',
    草稿箱: 'draft',
    垃圾邮件: 'junk',
    已删除: 'trash',
    已加星标: 'star',
  };
  return icons[mailStore.folderDisplayName(name)] || 'folder';
}

function mobileFolderCount(folder: any) {
  const displayName = mailStore.folderDisplayName(folder.name);
  return ['已发送', '草稿箱', '已删除'].includes(displayName)
    ? Number(folder.total_count || 0)
    : Number(folder.unread_count || 0);
}

function selectMailNavigation(detail: MailNavigation) {
  emit('mail-navigation', detail);
  emit('close-mobile');
}
</script>

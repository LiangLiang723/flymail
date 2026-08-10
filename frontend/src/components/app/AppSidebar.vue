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

    <div class="sidebar-primary-actions">
      <button
        class="sidebar-row sidebar-compose-action"
        type="button"
        title="写邮件"
        aria-label="写邮件"
        :disabled="mailStore.accounts.length === 0"
        @click="emit('compose')"
      >
        <span class="sidebar-row-icon">
          <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M12 5v14" />
            <path d="M5 12h14" />
          </svg>
        </span>
        <span class="sidebar-label-pane">写邮件</span>
      </button>

      <button
        v-if="unifiedInboxEnabled"
        class="sidebar-row sidebar-mail-entry"
        :class="{ active: currentView === 'unified' }"
        type="button"
        :title="collapsed && !mobile ? '聚合收件箱' : undefined"
        @click="emit('navigate', 'unified')"
      >
        <span class="sidebar-row-icon"><AppIcon name="inbox" :size="19" /></span>
        <span class="sidebar-label-pane">聚合收件箱</span>
      </button>
    </div>

    <div class="sidebar-scroll sidebar-mail-navigation">
      <section class="sidebar-mail-accounts" aria-labelledby="sidebar-accounts-title">
        <h3 id="sidebar-accounts-title" class="sidebar-section-title">邮箱账号</h3>
        <div class="sidebar-account-scroll" :class="{ 'has-scroll': mailStore.accounts.length > 5 }">
        <p v-if="mailStore.accounts.length === 0" class="sidebar-empty-copy">暂无邮箱账号</p>

        <div
          v-for="account in mailStore.accounts"
          :key="account.id"
          class="sidebar-account-row"
          :class="{ 'is-reauth': mailStore.reauthAccountIds.has(account.id) }"
        >
          <button
            type="button"
            class="sidebar-row sidebar-account-item"
            :class="{
              active: currentView === 'mail' && mailStore.currentAccountId === account.id,
              'is-context': mailStore.currentAccountId === account.id,
            }"
            :title="collapsed && !mobile ? accountDisplayName(account) : account.email"
            @click="emit('select-account', account.id)"
          >
            <span class="sidebar-row-icon sidebar-account-icon">
              <AccountIcon :account="account" :size="30" decorative />
            </span>
            <span class="sidebar-label-pane sidebar-account-copy">
              <strong>{{ accountDisplayName(account) }}</strong>
              <small>{{ account.email }}</small>
            </span>
          </button>
          <button
            v-if="mailStore.reauthAccountIds.has(account.id)"
            class="sidebar-account-reauth"
            type="button"
            title="重新授权"
            :aria-label="`重新授权 ${accountDisplayName(account)}`"
            @click="requestReauthorization(account.id)"
          >
            <AppIcon name="sync" :size="15" />
          </button>
        </div>
        </div>
      </section>

      <section
        v-if="mailStore.accounts.length > 0"
        class="sidebar-mail-folders"
        aria-labelledby="sidebar-folders-title"
      >
        <h3 id="sidebar-folders-title" class="sidebar-section-title">文件夹</h3>
        <div class="sidebar-folder-scroll">
          <button
            v-for="folder in mailStore.folders"
            :key="folder.path"
            type="button"
            class="sidebar-row sidebar-folder-item"
            :class="{ active: currentView === 'mail' && mailStore.currentFolder === folder.path }"
            :title="collapsed && !mobile ? mailStore.folderDisplayName(folder.name) : undefined"
            @click="emit('select-folder', folder.path)"
          >
            <span class="sidebar-row-icon">
              <span v-if="folderIconName(folder.name) === 'folder'" class="sidebar-folder-glyph" aria-hidden="true">
                <AppIcon name="folder" :size="18" />
                <span class="sidebar-folder-initial">{{ folderLetter(folder.name) }}</span>
              </span>
              <AppIcon v-else :name="folderIconName(folder.name)" :size="17" />
            </span>
            <span class="sidebar-label-pane sidebar-folder-copy">
              <span>{{ mailStore.folderDisplayName(folder.name) }}</span>
              <small>{{ folderCount(folder) }}</small>
            </span>
          </button>
        </div>
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
    </div>
  </aside>
</template>

<script setup lang="ts">
import AccountIcon from '../account/AccountIcon.vue';
import AppIcon from '../AppIcon.vue';
import UserMenu from './UserMenu.vue';
import { useMailStore } from '../../stores/mail';

defineProps<{
  collapsed: boolean;
  mobile: boolean;
  mobileOpen: boolean;
  currentView: string;
  unifiedInboxEnabled: boolean;
  user: {
    username: string;
    nickname?: string;
    display_name?: string;
    avatar_url?: string;
    role: string;
  } | null;
}>();

const emit = defineEmits<{
  'toggle-collapse': [];
  'close-mobile': [];
  compose: [];
  navigate: [key: string];
  'select-account': [accountId: string];
  'select-folder': [path: string];
  'reauthorize-account': [accountId: string];
  'open-notifications': [];
  'change-password': [];
  logout: [];
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

function folderLetter(name: string) {
  return String(mailStore.folderDisplayName(name) || name || '文').trim().slice(0, 1).toUpperCase();
}

function folderCount(folder: any) {
  const displayName = mailStore.folderDisplayName(folder.name);
  return ['已发送', '草稿箱', '已删除'].includes(displayName)
    ? Number(folder.total_count || 0)
    : Number(folder.unread_count || 0);
}

function requestReauthorization(accountId: string) {
  emit('reauthorize-account', accountId);
  emit('close-mobile');
}
</script>

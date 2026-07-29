<template>
  <LoginView v-if="!authReady || !currentUser" @success="handleLoginSuccess" />

  <div
    v-else
    class="app-shell"
    :class="{
      'sidebar-collapsed': sidebarCollapsed && !isMobileLayout,
      'mobile-sidebar-open': mobileSidebarOpen,
    }"
  >
    <button
      v-if="isMobileLayout && mobileSidebarOpen"
      class="mobile-sidebar-backdrop"
      type="button"
      aria-label="关闭导航"
      @click="mobileSidebarOpen = false"
    ></button>

    <aside class="sidebar" :aria-hidden="isMobileLayout && !mobileSidebarOpen">
      <div class="sidebar-header">
        <div class="brand">
          <img src="/icon.png" alt="FlyMail" class="brand-logo" />
          <div class="brand-copy">
            <div class="brand-name">FlyMail</div>
            <div class="brand-subtitle">Docker 多用户版</div>
          </div>
        </div>
        <button
          class="sidebar-toggle"
          type="button"
          :title="sidebarCollapsed ? '展开主导航' : '折叠主导航'"
          :aria-label="sidebarCollapsed ? '展开主导航' : '折叠主导航'"
          @click="toggleSidebar"
        >
          <AppIcon :name="sidebarCollapsed ? 'panel-left-open' : 'panel-left-close'" :size="19" />
        </button>
        <button class="sidebar-mobile-close" type="button" aria-label="关闭导航" @click="mobileSidebarOpen = false">
          <AppIcon name="close" :size="20" />
        </button>
      </div>

      <div class="nav-scroll">
        <nav class="nav-list" aria-label="主导航">
          <button
            v-for="item in navItems"
            :key="item.key"
            class="nav-item"
            :class="{ active: currentView === item.key }"
            :title="sidebarCollapsed && !isMobileLayout ? item.label : undefined"
            @click="navigateFromSidebar(item.key)"
          >
            <AppIcon :name="item.icon" :size="18" />
            <span class="nav-item-label">{{ item.label }}</span>
          </button>
        </nav>

        <section v-if="isMobileLayout && currentView === 'mail'" class="mobile-mail-navigation">
          <div class="mobile-mail-navigation-title">邮箱账号</div>
          <div v-for="account in mailStore.accounts" :key="account.id" class="mobile-account-row">
            <button
              type="button"
              class="mobile-account-item"
              :class="{ active: mailStore.currentAccountId === account.id }"
              @click="selectMobileMailNavigation({ type: 'account', id: account.id })"
            >
              <span class="mobile-account-avatar">{{ accountInitial(account) }}</span>
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
              @click="selectMobileMailNavigation({ type: 'reauth', id: account.id })"
            >
              <AppIcon name="sync" :size="15" />
            </button>
          </div>

          <div class="mobile-mail-navigation-title folder-title">文件夹</div>
          <button
            v-for="folder in mailStore.folders"
            :key="folder.path"
            type="button"
            class="mobile-folder-item"
            :class="{ active: mailStore.currentFolder === folder.path }"
            @click="selectMobileMailNavigation({ type: 'folder', path: folder.path })"
          >
            <AppIcon :name="folderIconName(folder.name)" :size="17" />
            <span>{{ mailStore.folderDisplayName(folder.name) }}</span>
            <small>{{ mobileFolderCount(folder) }}</small>
          </button>
        </section>
      </div>

      <div class="sidebar-bottom">
        <div class="sidebar-actions">
          <button class="sidebar-action notification-button" type="button" @click="toggleNotifications" title="通知中心" aria-label="通知中心">
            <AppIcon name="bell" :size="19" />
            <span class="sidebar-action-label">通知中心</span>
            <strong v-if="mailStore.unreadNotificationCount">{{ mailStore.unreadNotificationCount > 99 ? '99+' : mailStore.unreadNotificationCount }}</strong>
          </button>

          <div class="user-menu">
            <button
              class="sidebar-profile-trigger"
              type="button"
              :aria-expanded="showUserMenu"
              aria-haspopup="menu"
              title="账号菜单"
              @click="toggleUserMenu"
            >
              <span class="user-avatar">{{ userInitial }}</span>
              <span class="sidebar-profile-copy">
                <strong>{{ currentUser.username }}</strong>
                <small>{{ currentUser.role === 'admin' ? '管理员' : '普通用户' }}</small>
              </span>
              <AppIcon class="profile-chevron" name="chevron-down" :size="15" />
            </button>
            <div v-if="showUserMenu" class="user-menu-popover" role="menu">
              <div class="user-menu-summary">
                <span class="user-avatar large">{{ userInitial }}</span>
                <span>
                  <strong>{{ currentUser.username }}</strong>
                  <small>{{ currentUser.role === 'admin' ? '管理员' : '普通用户' }}</small>
                </span>
              </div>
              <button type="button" role="menuitem" @click="changePassword">
                <AppIcon name="lock" :size="17" />
                <span>修改密码</span>
              </button>
              <button type="button" role="menuitem" class="logout-item" @click="logout">
                <AppIcon name="logout" :size="17" />
                <span>退出登录</span>
              </button>
            </div>
          </div>
        </div>

        <div class="sidebar-footer">
          <span>FlyMail</span>
          <span>v{{ appVersion }}</span>
        </div>
      </div>
    </aside>

    <div class="main">
      <button
        v-if="isMobileLayout && !mobileSidebarOpen && !showNotifications"
        class="mobile-sidebar-launcher"
        type="button"
        title="打开导航"
        aria-label="打开导航"
        @click="toggleSidebar"
      >
        <AppIcon name="menu" :size="19" />
      </button>

      <main class="content" :class="`content-${currentView}`">
        <ComposeEmail v-if="currentView === 'compose'" @discard="returnToMail" @sent="returnToMail" />
        <template v-else-if="currentView === 'mail'">
          <KeepAlive>
            <MailList />
          </KeepAlive>
        </template>
        <UnifiedInbox v-else-if="currentView === 'unified'" />
        <HistorySync v-else-if="currentView === 'history-sync'" />
        <AccountList v-else-if="currentView === 'accounts'" />
        <ContactList v-else-if="currentView === 'contacts'" />
        <Backup v-else-if="currentView === 'backup'" />
        <UserManagement v-else-if="currentView === 'users' && isAdmin" />
        <Settings v-else-if="currentView === 'settings'" />
        <NotificationSettings v-else-if="currentView === 'notifications'" />
        <About v-else-if="currentView === 'about'" />
      </main>
    </div>

    <div v-if="showNotifications" class="notification-overlay" @click.self="showNotifications = false">
      <aside class="notification-drawer">
        <header class="notification-header">
          <div><h3>通知中心</h3><span>{{ mailStore.unreadNotificationCount }} 条未读</span></div>
          <button type="button" @click="showNotifications = false">×</button>
        </header>
        <div class="notification-tools">
          <button type="button" @click="mailStore.markAllNotificationsRead()">全部已读</button>
          <button type="button" class="danger-text" @click="mailStore.clearNotifications()">清空</button>
        </div>
        <div v-if="!mailStore.notifications.length" class="notification-empty">暂无通知</div>
        <div v-else class="notification-list">
          <button
            v-for="item in mailStore.notifications"
            :key="item.id"
            type="button"
            class="notification-item"
            :class="{ unread: !item.read }"
            @click="openNotification(item)"
          >
            <span class="notification-dot"></span>
            <span class="notification-content">
              <strong>{{ item.subject || item.message || notificationTitle(item) }}</strong>
              <small>{{ item.from_addr || item.email }}<template v-if="item.batch_count && item.batch_count > 1"> · {{ item.batch_count }} 封</template></small>
              <em v-if="item.body_preview">{{ item.body_preview }}</em>
            </span>
            <time>{{ formatNotificationTime(item.time) }}</time>
          </button>
        </div>
      </aside>
    </div>

    <div class="toast-container">
      <transition-group name="toast">
        <div v-for="t in uiStore.toasts" :key="t.id" class="toast-item" :class="'toast-' + t.type">
          {{ t.message }}
        </div>
      </transition-group>
    </div>

    <div v-if="uiStore.confirmVisible" class="confirm-overlay" @click.self="uiStore.confirmCancel()">
      <div class="confirm-dialog">
        <h3 class="confirm-title">{{ uiStore.confirmOptions.title }}</h3>
        <p class="confirm-message">{{ uiStore.confirmOptions.message }}</p>
        <div class="confirm-actions">
          <button class="btn btn-secondary" @click="uiStore.confirmCancel()">
            {{ uiStore.confirmOptions.cancelText || '取消' }}
          </button>
          <button
            class="btn"
            :class="uiStore.confirmOptions.danger ? 'btn-danger' : 'btn-primary'"
            @click="uiStore.confirmOk()"
          >
            {{ uiStore.confirmOptions.confirmText || '确定' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import AppIcon from './components/AppIcon.vue';
import About from './views/About.vue';
import AccountList from './views/AccountList.vue';
import Backup from './views/Backup.vue';
import ComposeEmail from './views/ComposeEmail.vue';
import ContactList from './views/ContactList.vue';
import HistorySync from './views/HistorySync.vue';
import LoginView from './views/LoginView.vue';
import MailList from './views/MailList.vue';
import NotificationSettings from './views/NotificationSettings.vue';
import Settings from './views/Settings.vue';
import UnifiedInbox from './views/UnifiedInbox.vue';
import UserManagement from './views/UserManagement.vue';
import { useMailStore } from './stores/mail';
import { useUIStore } from './stores/ui';
import { useWebSocket } from './composables/useWebSocket';
import api from './utils/api';

const mailStore = useMailStore();
const uiStore = useUIStore();

const currentUser = ref<any>(null);
const authReady = ref(false);
const showNotifications = ref(false);
const showUserMenu = ref(false);
const isMobileLayout = ref(window.innerWidth <= 960);
const sidebarCollapsed = ref(localStorage.getItem('flymail_sidebar_collapsed') === '1');
const mobileSidebarOpen = ref(false);
const appVersion = import.meta.env.VITE_APP_VERSION || '0.0.0';

function handleGlobalWsMessage(data: any) {
  if (data.type === 'new_mail') {
    mailStore.addNotification(
      data.provider || '',
      data.email || '',
      data.folder || 'INBOX',
      data.notification_id,
      'new_mail',
      data.message || data.subject || '',
      {
        account_id: data.account_id || '',
        message_cache_id: data.message_cache_id || '',
        message_uid: Number(data.message_uid || 0),
        subject: data.subject || '',
        from_addr: data.from_addr || '',
        body_preview: data.body_preview || '',
        has_attachments: Boolean(data.has_attachments),
        batch_count: Number(data.batch_count || 1),
      },
    );
  } else if (['schedule_success', 'schedule_failed', 'backup_success', 'backup_failed'].includes(data.type)) {
    mailStore.addNotification(
      data.provider || '',
      data.email || '',
      data.folder || '',
      data.notification_id,
      data.type,
      data.message || '',
      {
        account_id: data.account_id || '',
        subject: data.subject || '',
      },
    );
  }
}

const { connect: connectGlobalWs, disconnect: disconnectGlobalWs } = useWebSocket(handleGlobalWsMessage);
const savedView = sessionStorage.getItem('flymail_view') || 'mail';
const currentView = ref(savedView === 'compose' ? 'mail' : savedView);

const isAdmin = computed(() => currentUser.value?.role === 'admin');
const userInitial = computed(() => String(currentUser.value?.username || 'U').trim().charAt(0).toUpperCase());

const navItems = computed(() => [
  { key: 'unified', label: '聚合收件箱', icon: 'inbox' },
  { key: 'mail', label: '邮件管理', icon: 'mail' },
  { key: 'contacts', label: '联系人', icon: 'contacts' },
  { key: 'history-sync', label: '同步管理', icon: 'sync' },
  { key: 'accounts', label: '账号管理', icon: 'accounts' },
  { key: 'backup', label: '邮件备份', icon: 'backup' },
  ...(isAdmin.value ? [{ key: 'users', label: '用户管理', icon: 'users' }] : []),
  { key: 'notifications', label: '第三方通知', icon: 'notifications' },
  { key: 'settings', label: '设置', icon: 'settings' },
  { key: 'about', label: '关于', icon: 'info' },
]);

function accountDisplayName(account: any) {
  return String(account?.remark || '').trim() || account?.email || '邮箱账号';
}

function accountInitial(account: any) {
  return accountDisplayName(account).trim().charAt(0).toUpperCase() || 'M';
}

function folderIconName(name: string) {
  const displayName = mailStore.folderDisplayName(name);
  const icons: Record<string, string> = {
    收件箱: 'inbox',
    已发送: 'send',
    草稿箱: 'draft',
    垃圾邮件: 'junk',
    已删除: 'trash',
    已加星标: 'star',
  };
  return icons[displayName] || 'folder';
}

function mobileFolderCount(folder: any) {
  const displayName = mailStore.folderDisplayName(folder.name);
  return ['已发送', '草稿箱', '已删除'].includes(displayName)
    ? Number(folder.total_count || 0)
    : Number(folder.unread_count || 0);
}

function navigateFromSidebar(key: string) {
  currentView.value = key;
  showUserMenu.value = false;
  mobileSidebarOpen.value = false;
}

function selectMobileMailNavigation(detail: { type: 'account' | 'reauth'; id: string } | { type: 'folder'; path: string }) {
  window.dispatchEvent(new CustomEvent('flymail-mail-navigation', { detail }));
  mobileSidebarOpen.value = false;
}

function toggleSidebar() {
  showNotifications.value = false;
  showUserMenu.value = false;
  if (isMobileLayout.value) {
    mobileSidebarOpen.value = !mobileSidebarOpen.value;
    return;
  }
  sidebarCollapsed.value = !sidebarCollapsed.value;
  localStorage.setItem('flymail_sidebar_collapsed', sidebarCollapsed.value ? '1' : '0');
}

function handleWindowResize() {
  const nextMobile = window.innerWidth <= 960;
  if (!nextMobile) mobileSidebarOpen.value = false;
  isMobileLayout.value = nextMobile;
}

function handleSidebarRequest() {
  if (isMobileLayout.value) mobileSidebarOpen.value = true;
}

function handleGlobalKeydown(event: KeyboardEvent) {
  if (event.key !== 'Escape') return;
  mobileSidebarOpen.value = false;
  showNotifications.value = false;
  showUserMenu.value = false;
}

async function bootstrapAfterLogin() {
  currentUser.value = await api.get('/auth/me');
  await mailStore.fetchUser();
  await mailStore.loadAccounts();
  await mailStore.loadNotifications();
  connectGlobalWs();
}

async function checkAuth() {
  try {
    await bootstrapAfterLogin();
  } catch {
    currentUser.value = null;
  } finally {
    authReady.value = true;
  }
}

async function handleLoginSuccess() {
  await bootstrapAfterLogin();
  authReady.value = true;
}

function toggleNotifications() {
  showUserMenu.value = false;
  mobileSidebarOpen.value = false;
  showNotifications.value = !showNotifications.value;
}

function toggleUserMenu() {
  showNotifications.value = false;
  showUserMenu.value = !showUserMenu.value;
}

async function logout() {
  showUserMenu.value = false;
  await api.post('/auth/logout');
  disconnectGlobalWs();
  currentUser.value = null;
  mailStore.accounts = [];
  sessionStorage.removeItem('flymail_view');
}

async function changePassword() {
  showUserMenu.value = false;
  const currentPassword = window.prompt('请输入当前密码');
  if (!currentPassword) return;
  const newPassword = window.prompt('请输入新密码');
  if (!newPassword) return;
  await api.post('/auth/change-password', {
    current_password: currentPassword,
    new_password: newPassword,
  });
  uiStore.success('密码已更新');
}

function returnToMail(payload?: { account_id?: string }) {
  currentView.value = 'mail';
  if (payload?.account_id) {
    nextTick(() => {
      window.dispatchEvent(new CustomEvent('flymail-sent-message', { detail: payload }));
    });
  }
}

function handleNavigate(event: Event) {
  const detail = (event as CustomEvent).detail;
  if (detail === 'compose') {
    currentView.value = 'compose';
  } else if (typeof detail === 'string') {
    currentView.value = detail;
  }
}

function notificationTitle(item: any) {
  if (item.type === 'schedule_success') return '定时邮件发送成功';
  if (item.type === 'schedule_failed') return '定时邮件发送失败';
  if (item.type === 'backup_success') return '邮件备份完成';
  if (item.type === 'backup_failed') return '邮件备份失败';
  return '收到新邮件';
}

function formatNotificationTime(value: number) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

async function openNotification(item: any) {
  await mailStore.markNotificationRead(item.id);
  showNotifications.value = false;
  if (!item.account_id || (!item.message_cache_id && !item.message_uid)) return;
  mailStore.setAccount(item.account_id);
  mailStore.setFolder(item.folder || 'INBOX');
  sessionStorage.setItem('flymail_pending_message', JSON.stringify({
    account_id: item.account_id,
    folder: item.folder || 'INBOX',
    id: item.message_cache_id || String(item.message_uid),
    uid: item.message_uid || undefined,
  }));
  currentView.value = 'mail';
}

onMounted(() => {
  window.addEventListener('flymail-navigate', handleNavigate);
  window.addEventListener('flymail-toggle-sidebar', handleSidebarRequest);
  window.addEventListener('resize', handleWindowResize);
  window.addEventListener('keydown', handleGlobalKeydown);
  checkAuth();
});

onUnmounted(() => {
  window.removeEventListener('flymail-navigate', handleNavigate);
  window.removeEventListener('flymail-toggle-sidebar', handleSidebarRequest);
  window.removeEventListener('resize', handleWindowResize);
  window.removeEventListener('keydown', handleGlobalKeydown);
});

watch(currentView, (value) => {
  mobileSidebarOpen.value = false;
  if (value === 'users' && !isAdmin.value) {
    currentView.value = 'mail';
    return;
  }
  if (value !== 'compose' && !navItems.value.some((item) => item.key === value)) {
    currentView.value = 'mail';
    return;
  }
  if (value !== 'compose') {
    sessionStorage.setItem('flymail_view', value);
  }
});
</script>

<style scoped>
.app-shell {
  height: 100vh;
  min-height: 100vh;
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  background: var(--bg-secondary);
  overflow: hidden;
  transition: grid-template-columns 180ms ease;
}

.app-shell.sidebar-collapsed {
  grid-template-columns: 68px minmax(0, 1fr);
}

.sidebar {
  width: 100%;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 14px 10px 12px;
  background: var(--bg-primary);
  border-right: 1px solid var(--border-color);
  overflow: hidden;
}

.mobile-sidebar-backdrop,
.mobile-sidebar-launcher,
.sidebar-mobile-close,
.mobile-mail-navigation {
  display: none;
}

.sidebar-header {
  min-height: 44px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.brand {
  min-width: 0;
  display: flex;
  align-items: center;
  flex: 1;
  gap: 10px;
  padding: 2px 4px;
  overflow: hidden;
}

.brand-logo {
  width: 36px;
  height: 36px;
  flex: 0 0 auto;
}

.brand-copy {
  min-width: 0;
  white-space: nowrap;
}

.brand-name {
  font-size: 19px;
  line-height: 1.2;
  font-weight: 700;
  letter-spacing: -0.02em;
}

.brand-subtitle {
  margin-top: 2px;
  color: var(--text-secondary);
  font-size: 11px;
}

.sidebar-toggle {
  width: 36px;
  height: 36px;
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 9px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition: background var(--transition-fast), border-color var(--transition-fast), color var(--transition-fast);
}

.sidebar-toggle:hover {
  border-color: var(--border-color);
  background: var(--bg-hover);
  color: var(--text-primary);
}

.nav-scroll {
  flex: 1;
  min-width: 0;
  min-height: 0;
  margin-top: 14px;
  overflow-x: hidden;
  overflow-y: auto;
}

.nav-list {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.nav-item {
  width: 100%;
  height: 40px;
  display: flex;
  align-items: center;
  gap: 11px;
  border: none;
  border-radius: 9px;
  text-align: left;
  padding: 0 11px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  font: inherit;
  font-size: 14px;
  transition: background var(--transition-fast), color var(--transition-fast), transform var(--transition-fast);
}

.nav-item:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.nav-item:active {
  transform: translateY(1px);
}

.nav-item.active {
  background: var(--bg-active);
  color: var(--color-accent);
  font-weight: 600;
}

.nav-item > svg {
  flex: 0 0 auto;
}

.nav-item-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sidebar-bottom {
  flex: 0 0 auto;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--border-color);
}

.sidebar-actions {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.sidebar-action,
.sidebar-profile-trigger {
  width: 100%;
  min-height: 42px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 10px;
  border: 1px solid transparent;
  border-radius: 9px;
  background: transparent;
  color: var(--text-secondary);
  text-align: left;
  cursor: pointer;
  font: inherit;
  transition: background var(--transition-fast), border-color var(--transition-fast), color var(--transition-fast);
}

.sidebar-action:hover,
.sidebar-profile-trigger:hover {
  border-color: var(--border-color);
  background: var(--bg-hover);
  color: var(--text-primary);
}

.sidebar-action-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}

.notification-button {
  position: relative;
}

.notification-button strong {
  position: absolute;
  top: 2px;
  right: 5px;
  min-width: 18px;
  height: 18px;
  padding: 0 4px;
  border: 2px solid var(--bg-primary);
  border-radius: 999px;
  background: var(--color-danger);
  color: var(--text-on-accent);
  font-size: 10px;
  line-height: 14px;
  text-align: center;
}

.user-menu {
  position: relative;
}

.sidebar-profile-trigger {
  padding-left: 6px;
}

.sidebar-profile-copy {
  min-width: 0;
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 1px;
}

.sidebar-profile-copy strong,
.sidebar-profile-copy small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sidebar-profile-copy strong {
  color: var(--text-primary);
  font-size: 13px;
}

.sidebar-profile-copy small {
  color: var(--text-tertiary);
  font-size: 10px;
}

.profile-chevron {
  flex: 0 0 auto;
}

.user-avatar {
  width: 30px;
  height: 30px;
  display: inline-grid;
  place-items: center;
  flex: 0 0 auto;
  border-radius: 50%;
  background: var(--color-accent);
  color: var(--text-on-accent);
  font-size: 13px;
  font-weight: 700;
}

.user-avatar.large {
  width: 38px;
  height: 38px;
  font-size: 15px;
}

.sidebar-footer {
  display: flex;
  justify-content: space-between;
  padding: 10px 10px 0;
  color: var(--text-tertiary);
  font-size: 10px;
}

.user-menu-popover {
  position: fixed;
  left: 230px;
  bottom: 24px;
  z-index: 9300;
  width: 220px;
  padding: 8px;
  border: 1px solid var(--border-color-strong);
  border-radius: 13px;
  background: var(--bg-card);
  box-shadow: var(--shadow-xl);
}

.app-shell.sidebar-collapsed .sidebar-header {
  min-height: 88px;
  flex-direction: column;
  justify-content: flex-start;
  gap: 7px;
}

.app-shell.sidebar-collapsed .brand {
  width: 100%;
  flex: 0 0 auto;
  justify-content: center;
  padding: 0;
}

.app-shell.sidebar-collapsed .brand-copy,
.app-shell.sidebar-collapsed .nav-item-label,
.app-shell.sidebar-collapsed .sidebar-action-label,
.app-shell.sidebar-collapsed .sidebar-profile-copy,
.app-shell.sidebar-collapsed .profile-chevron,
.app-shell.sidebar-collapsed .sidebar-footer {
  display: none;
}

.app-shell.sidebar-collapsed .nav-item,
.app-shell.sidebar-collapsed .sidebar-action,
.app-shell.sidebar-collapsed .sidebar-profile-trigger {
  justify-content: center;
  gap: 0;
  padding: 0;
}

.app-shell.sidebar-collapsed .notification-button strong {
  top: 1px;
  right: 0;
}

.app-shell.sidebar-collapsed .user-menu-popover {
  left: 78px;
}

.main {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.user-menu-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 8px 12px;
  margin-bottom: 5px;
  border-bottom: 1px solid var(--border-color);
}

.user-menu-summary > span:last-child {
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.user-menu-summary strong,
.user-menu-summary small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-menu-summary strong {
  font-size: 13px;
}

.user-menu-summary small {
  color: var(--text-secondary);
  font-size: 11px;
}

.user-menu-popover > button {
  width: 100%;
  height: 38px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--text-primary);
  cursor: pointer;
  font: inherit;
  font-size: 13px;
}

.user-menu-popover > button:hover {
  background: var(--bg-hover);
}

.user-menu-popover .logout-item {
  color: var(--color-danger);
}

.notification-overlay {
  position: fixed;
  inset: 0;
  z-index: 9000;
  display: flex;
  justify-content: flex-end;
  background: rgba(15, 23, 42, 0.28);
}

.notification-drawer {
  width: min(420px, 100vw);
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--bg-primary);
  box-shadow: var(--shadow-xl);
}

.notification-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px;
  border-bottom: 1px solid var(--border-color);
}

.notification-header h3 { margin: 0 0 4px; }
.notification-header span { color: var(--text-secondary); font-size: 13px; }
.notification-header button { border: 0; background: transparent; color: var(--text-primary); font-size: 28px; cursor: pointer; }
.notification-tools { display: flex; justify-content: flex-end; gap: 12px; padding: 10px 16px; border-bottom: 1px solid var(--border-color); }
.notification-tools button { border: 0; background: transparent; color: var(--color-accent); cursor: pointer; }
.notification-tools .danger-text { color: var(--color-danger); }
.notification-list { flex: 1; overflow-y: auto; }
.notification-empty { flex: 1; display: grid; place-items: center; color: var(--text-secondary); }
.notification-item { width: 100%; display: grid; grid-template-columns: 10px minmax(0, 1fr) auto; gap: 10px; padding: 14px 16px; border: 0; border-bottom: 1px solid var(--border-color); background: var(--bg-primary); color: var(--text-primary); text-align: left; cursor: pointer; }
.notification-item:hover { background: var(--bg-hover); }
.notification-item.unread { background: var(--bg-active); }
.notification-dot { width: 7px; height: 7px; margin-top: 6px; border-radius: 50%; background: transparent; }
.notification-item.unread .notification-dot { background: var(--color-accent); }
.notification-content { min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.notification-content strong, .notification-content small, .notification-content em { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.notification-content small, .notification-content em { color: var(--text-secondary); font-size: 12px; font-style: normal; }
.notification-item time { color: var(--text-tertiary); font-size: 11px; white-space: nowrap; }

.content {
  flex: 1;
  min-height: 0;
  min-width: 0;
  width: 100%;
  display: flex;
  padding: 20px 28px 28px;
  overflow: hidden;
}

.content-mail {
  padding: 20px 28px 28px;
}

.content-compose,
.content-accounts,
.content-history-sync,
.content-users,
.content-settings,
.content-unified,
.content-contacts,
.content-backup,
.content-notifications,
.content-about {
  padding: 0;
  width: 100%;
}

.btn {
  height: 40px;
  border: none;
  border-radius: 10px;
  padding: 0 14px;
  cursor: pointer;
}

.btn-secondary {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

.btn-primary {
  background: var(--color-accent);
  color: var(--text-on-accent);
}

.btn-danger {
  background: var(--color-danger);
  color: var(--text-on-accent);
}

.toast-container {
  position: fixed;
  right: 20px;
  bottom: 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  z-index: 10000;
}

.toast-item {
  min-width: 220px;
  padding: 12px 14px;
  border-radius: 10px;
  color: var(--text-on-accent);
  box-shadow: var(--shadow-lg);
}

.toast-success {
  background: var(--color-success);
}

.toast-error {
  background: var(--color-danger);
}

.toast-warning {
  background: var(--color-warning);
}

.toast-info {
  background: var(--color-accent);
}

.confirm-overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.36);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9000;
  padding: 20px;
}

.confirm-dialog {
  width: min(100%, 420px);
  background: var(--bg-card);
  border-radius: 14px;
  padding: 20px;
  box-shadow: var(--shadow-xl);
}

.confirm-title {
  margin: 0;
  font-size: 18px;
  color: var(--text-primary);
}

.confirm-message {
  margin: 10px 0 0;
  font-size: 14px;
  line-height: 1.6;
  color: var(--text-secondary);
}

.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 18px;
}

@media (max-width: 960px) {
  .app-shell,
  .app-shell.sidebar-collapsed {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: minmax(0, 1fr);
    height: 100dvh;
    min-height: 100dvh;
    overflow: hidden;
    transition: none;
  }

  .sidebar,
  .app-shell.sidebar-collapsed .sidebar {
    position: fixed;
    inset: 0 auto 0 0;
    z-index: 9200;
    width: min(86vw, 320px);
    padding: 18px 14px 14px;
    border-right: 1px solid var(--border-color-strong);
    border-bottom: 0;
    box-shadow: var(--shadow-xl);
    opacity: 1;
    overflow: hidden;
    transform: translateX(-105%);
    pointer-events: none;
    transition: transform 180ms ease;
  }

  .app-shell.mobile-sidebar-open .sidebar {
    transform: translateX(0);
    pointer-events: auto;
  }

  .mobile-sidebar-backdrop {
    position: fixed;
    inset: 0;
    z-index: 9100;
    display: block;
    padding: 0;
    border: 0;
    background: rgba(5, 10, 18, 0.58);
  }

  .mobile-sidebar-launcher {
    position: fixed;
    top: 50%;
    left: 0;
    z-index: 9050;
    width: 34px;
    height: 48px;
    display: grid;
    place-items: center;
    padding: 0;
    border: 1px solid var(--border-color-strong);
    border-left: 0;
    border-radius: 0 12px 12px 0;
    background: color-mix(in srgb, var(--bg-card) 92%, transparent);
    color: var(--text-secondary);
    box-shadow: var(--shadow-md);
    transform: translateY(-50%);
    backdrop-filter: blur(12px);
  }

  .sidebar-header {
    min-height: 44px;
  }

  .brand {
    padding: 0;
  }

  .brand-logo {
    width: 40px;
    height: 40px;
  }

  .brand-name {
    font-size: 20px;
  }

  .brand-subtitle {
    font-size: 12px;
  }

  .sidebar-toggle {
    display: none;
  }

  .sidebar-mobile-close {
    width: 36px;
    height: 36px;
    display: grid;
    place-items: center;
    flex: 0 0 auto;
    padding: 0;
    border: 0;
    border-radius: 9px;
    background: transparent;
    color: var(--text-secondary);
  }

  .nav-scroll {
    flex: 1;
    min-height: 0;
    margin-top: 14px;
    padding-right: 2px;
    overflow-x: hidden;
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
  }

  .nav-list {
    gap: 3px;
    min-width: 0;
    padding-bottom: 12px;
  }

  .nav-item {
    width: 100%;
    height: 42px;
    flex: 0 0 auto;
    padding: 0 12px;
    border-radius: 9px;
    background: transparent;
    white-space: normal;
  }

  .nav-item.active {
    background: var(--bg-active);
    color: var(--color-accent);
  }

  .mobile-mail-navigation {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 18px 0 8px;
    border-top: 1px solid var(--border-color);
  }

  .mobile-mail-navigation-title {
    padding: 0 10px 6px;
    color: var(--text-tertiary);
    font-size: 11px;
    font-weight: 650;
    letter-spacing: 0.08em;
  }

  .mobile-mail-navigation-title.folder-title {
    padding-top: 14px;
  }

  .mobile-account-row {
    position: relative;
    display: flex;
    align-items: center;
  }

  .mobile-account-item,
  .mobile-folder-item {
    width: 100%;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 10px;
    border: 0;
    border-radius: 9px;
    background: transparent;
    color: var(--text-secondary);
    text-align: left;
    font: inherit;
  }

  .mobile-account-item {
    padding-right: 42px;
  }

  .mobile-account-reauth {
    position: absolute;
    right: 7px;
    width: 32px;
    height: 32px;
    display: grid;
    place-items: center;
    padding: 0;
    border: 0;
    border-radius: 8px;
    background: transparent;
    color: var(--color-warning);
  }

  .mobile-account-item.active,
  .mobile-folder-item.active {
    background: var(--bg-active);
    color: var(--color-accent);
  }

  .mobile-account-avatar {
    width: 30px;
    height: 30px;
    display: grid;
    place-items: center;
    flex: 0 0 auto;
    border-radius: 50%;
    background: var(--bg-tertiary);
    color: var(--text-primary);
    font-size: 12px;
    font-weight: 700;
  }

  .mobile-account-item.active .mobile-account-avatar {
    background: var(--color-accent);
    color: var(--text-on-accent);
  }

  .mobile-account-copy {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .mobile-account-copy strong,
  .mobile-account-copy small {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .mobile-account-copy strong {
    color: inherit;
    font-size: 13px;
  }

  .mobile-account-copy small {
    color: var(--text-tertiary);
    font-size: 10px;
  }

  .mobile-folder-item > span {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .mobile-folder-item > small {
    color: var(--text-tertiary);
    font-size: 11px;
  }

  .sidebar-bottom {
    margin-top: 8px;
  }

  .sidebar-footer {
    display: flex;
  }

  .user-menu-popover {
    position: absolute;
    left: 0;
    right: 0;
    bottom: calc(100% + 8px);
    width: auto;
  }

  .main {
    width: 100%;
    height: 100dvh;
    min-height: 0;
    overflow: hidden;
  }

  .content {
    width: 100%;
    min-width: 0;
    padding: 0 0 env(safe-area-inset-bottom, 0px);
    overflow: auto;
  }

  .content-mail {
    padding: 0 0 env(safe-area-inset-bottom, 0px);
    overflow: hidden;
  }
}

@media (prefers-reduced-motion: reduce) {
  .app-shell,
  .sidebar,
  .sidebar-toggle {
    transition: none;
  }
}
</style>

<template>
  <LoginView v-if="!authReady || !currentUser" @success="handleLoginSuccess" />

  <div v-else class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <img src="/icon.png" alt="FlyMail" class="brand-logo" />
        <div>
          <div class="brand-name">FlyMail</div>
          <div class="brand-subtitle">Docker 多用户版</div>
        </div>
      </div>

      <div class="nav-scroll">
        <nav class="nav">
          <button
            v-for="item in navItems"
            :key="item.key"
            class="nav-item"
            :class="{ active: currentView === item.key }"
            @click="currentView = item.key"
          >
            {{ item.label }}
          </button>
        </nav>
      </div>
    </aside>

    <div class="main">
      <header class="topbar">
        <div>
          <h1>{{ currentTitle }}</h1>
          <p>{{ currentUser.username }} · {{ currentUser.role === 'admin' ? '管理员' : '普通用户' }}</p>
        </div>
        <div class="topbar-actions">
          <button class="notification-button" type="button" @click="showNotifications = !showNotifications" title="通知中心">
            <span>通知</span>
            <strong v-if="mailStore.unreadNotificationCount">{{ mailStore.unreadNotificationCount > 99 ? '99+' : mailStore.unreadNotificationCount }}</strong>
          </button>
          <button class="btn btn-secondary" @click="changePassword">修改密码</button>
          <button class="btn btn-secondary" @click="logout">退出登录</button>
        </div>
      </header>

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

const navItems = computed(() => {
  const items = [
    { key: 'unified', label: '聚合收件箱' },
    { key: 'mail', label: '邮件管理' },
    { key: 'history-sync', label: '同步管理' },
    { key: 'accounts', label: '账号管理' },
    { key: 'contacts', label: '联系人' },
    { key: 'backup', label: '邮件备份' },
    { key: 'settings', label: '设置' },
    { key: 'notifications', label: '第三方通知' },
    { key: 'about', label: '关于' },
  ];
  if (isAdmin.value) {
    items.splice(6, 0, { key: 'users', label: '用户管理' });
  }
  return items;
});

const currentTitle = computed(() => {
  if (currentView.value === 'compose') return '写邮件';
  return navItems.value.find((item) => item.key === currentView.value)?.label || 'FlyMail';
});

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

async function logout() {
  await api.post('/auth/logout');
  disconnectGlobalWs();
  currentUser.value = null;
  mailStore.accounts = [];
  sessionStorage.removeItem('flymail_view');
}

async function changePassword() {
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
  checkAuth();
});

onUnmounted(() => {
  window.removeEventListener('flymail-navigate', handleNavigate);
});

watch(currentView, (value) => {
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
  grid-template-columns: 240px minmax(0, 1fr);
  background: var(--bg-secondary);
  overflow: hidden;
}

.sidebar {
  padding: 24px 18px;
  background: var(--bg-primary);
  border-right: 1px solid var(--border-color);
  overflow-y: auto;
  min-width: 0;
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 24px;
}

.brand-logo {
  width: 44px;
  height: 44px;
}

.brand-name {
  font-size: 24px;
  font-weight: 700;
}

.brand-subtitle {
  color: var(--text-secondary);
  font-size: 13px;
}

.nav-scroll {
  min-width: 0;
}

.nav {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.nav-item {
  height: 42px;
  border: none;
  border-radius: 10px;
  text-align: left;
  padding: 0 14px;
  background: transparent;
  color: var(--text-primary);
  cursor: pointer;
  font-size: 14px;
}

.nav-item:hover {
  background: var(--bg-hover);
}

.nav-item.active {
  background: var(--color-accent);
  color: var(--text-on-accent);
}

.main {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 24px 28px 0;
}

.topbar h1 {
  margin: 0;
  font-size: 28px;
}

.topbar p {
  margin: 6px 0 0;
  color: var(--text-secondary);
}

.topbar-actions {
  display: flex;
  gap: 10px;
  align-items: center;
}

.notification-button {
  position: relative;
  height: 40px;
  padding: 0 14px;
  border: 0;
  border-radius: 10px;
  background: var(--bg-tertiary);
  color: var(--text-primary);
  cursor: pointer;
}

.notification-button strong {
  position: absolute;
  top: -7px;
  right: -7px;
  min-width: 20px;
  height: 20px;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--color-danger);
  color: var(--text-on-accent);
  font-size: 11px;
  line-height: 20px;
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
  .app-shell {
    grid-template-columns: 1fr;
    grid-template-rows: auto auto minmax(0, 1fr);
    height: 100dvh;
    overflow-y: auto;
  }

  .sidebar {
    border-right: none;
    border-bottom: 1px solid var(--border-color);
    padding: 18px 16px 14px;
    overflow: visible;
  }

  .brand {
    margin-bottom: 16px;
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

  .nav-scroll {
    overflow-x: auto;
    overflow-y: hidden;
    padding-right: 4px;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: none;
  }

  .nav-scroll::-webkit-scrollbar {
    display: none;
  }

  .nav {
    display: inline-flex;
    flex-direction: row;
    gap: 8px;
    min-width: max-content;
    padding-bottom: 2px;
  }

  .nav-item {
    flex: 0 0 auto;
    height: 38px;
    padding: 0 14px;
    white-space: nowrap;
    border-radius: 999px;
    background: var(--bg-tertiary);
  }

  .nav-item.active {
    background: var(--color-accent);
    color: var(--text-on-accent);
  }

  .topbar {
    flex-direction: column;
    align-items: flex-start;
    gap: 14px;
    padding: 18px 16px 0;
  }

  .topbar h1 {
    font-size: 22px;
  }

  .topbar p {
    margin-top: 4px;
    font-size: 14px;
  }

  .topbar-actions {
    width: 100%;
    flex-wrap: wrap;
    gap: 8px;
  }

  .topbar-actions .btn {
    flex: 0 0 auto;
    height: 36px;
    padding: 0 12px;
    border-radius: 9px;
  }

  .main {
    overflow: visible;
  }

  .content {
    padding: 0 0 calc(16px + env(safe-area-inset-bottom, 0px));
    overflow: visible;
    width: 100%;
  }

  .content-mail {
    padding: 12px 0 calc(16px + env(safe-area-inset-bottom, 0px));
  }
}
</style>

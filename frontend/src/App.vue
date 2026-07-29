<template>
  <AuthGate :state="authState" :error-message="authErrorMessage" @retry="checkAuth">
    <template #anonymous>
      <LoginView @success="handleLoginSuccess" />
    </template>

    <div
      class="app-shell"
      :class="{
        'sidebar-collapsed': sidebarCollapsed && !isMobileLayout,
        'mobile-sidebar-open': mobileSidebarOpen,
      }"
    >
      <AppSidebar
        :collapsed="sidebarCollapsed"
        :mobile="isMobileLayout"
        :mobile-open="mobileSidebarOpen"
        :current-view="currentView"
        :nav-items="navItems"
        :user="currentUser"
        :app-version="appVersion"
        @toggle-collapse="toggleSidebar"
        @close-mobile="mobileSidebarOpen = false"
        @navigate="navigateFromSidebar"
        @open-notifications="toggleNotifications"
        @change-password="changePassword"
        @logout="logout"
        @mail-navigation="selectMobileMailNavigation"
      />

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

      <NotificationDrawer
        :open="showNotifications"
        :notifications="mailStore.notifications"
        :unread-count="mailStore.unreadNotificationCount"
        :title-for="notificationTitle"
        :format-time="formatNotificationTime"
        @close="showNotifications = false"
        @mark-all-read="mailStore.markAllNotificationsRead()"
        @clear="mailStore.clearNotifications()"
        @open-item="openNotification"
      />

      <div class="toast-container" aria-live="polite" aria-atomic="false">
        <transition-group name="toast">
          <div v-for="toast in uiStore.toasts" :key="toast.id" class="toast-item" :class="`toast-${toast.type}`">
            {{ toast.message }}
          </div>
        </transition-group>
      </div>

      <div v-if="uiStore.confirmVisible" class="confirm-overlay" @click.self="uiStore.confirmCancel()">
        <div class="confirm-dialog" role="dialog" aria-modal="true" :aria-labelledby="'confirm-dialog-title'">
          <h3 id="confirm-dialog-title" class="confirm-title">{{ uiStore.confirmOptions.title }}</h3>
          <p class="confirm-message">{{ uiStore.confirmOptions.message }}</p>
          <div class="confirm-actions">
            <button class="btn btn-secondary" type="button" @click="uiStore.confirmCancel()">
              {{ uiStore.confirmOptions.cancelText || '取消' }}
            </button>
            <button
              class="btn"
              :class="uiStore.confirmOptions.danger ? 'btn-danger' : 'btn-primary'"
              type="button"
              @click="uiStore.confirmOk()"
            >
              {{ uiStore.confirmOptions.confirmText || '确定' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </AuthGate>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import AppIcon from './components/AppIcon.vue';
import AppSidebar from './components/app/AppSidebar.vue';
import AuthGate from './components/app/AuthGate.vue';
import NotificationDrawer from './components/app/NotificationDrawer.vue';
import { useWebSocket } from './composables/useWebSocket';
import { useMailStore } from './stores/mail';
import { useUIStore } from './stores/ui';
import api from './utils/api';
import { classifyAuthError, normalizeApiError, type AuthState } from './utils/auth-state';
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

interface CurrentUser {
  id: string;
  uid: string;
  username: string;
  role: string;
  status: string;
}

type MailNavigation =
  | { type: 'account' | 'reauth'; id: string }
  | { type: 'folder'; path: string };

const mailStore = useMailStore();
const uiStore = useUIStore();
const currentUser = ref<CurrentUser | null>(null);
const authState = ref<AuthState>('booting');
const authErrorMessage = ref('');
const showNotifications = ref(false);
const isMobileLayout = ref(window.innerWidth <= 960);
const sidebarCollapsed = ref(localStorage.getItem('flymail_sidebar_collapsed') === '1');
const mobileSidebarOpen = ref(false);
const appVersion = import.meta.env.VITE_APP_VERSION || '0.0.0';
const savedView = sessionStorage.getItem('flymail_view') || 'mail';
const currentView = ref(savedView === 'compose' ? 'mail' : savedView);

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
    return;
  }

  if (['schedule_success', 'schedule_failed', 'backup_success', 'backup_failed'].includes(data.type)) {
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

const isAdmin = computed(() => currentUser.value?.role === 'admin');
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

function navigateFromSidebar(key: string) {
  currentView.value = key;
  mobileSidebarOpen.value = false;
}

function selectMobileMailNavigation(detail: MailNavigation) {
  window.dispatchEvent(new CustomEvent('flymail-mail-navigation', { detail }));
  mobileSidebarOpen.value = false;
}

function toggleSidebar() {
  showNotifications.value = false;
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
}

async function loadAuthenticatedSession() {
  currentUser.value = await api.get('/auth/me');
  await mailStore.fetchUser();
  await mailStore.loadAccounts();
  await mailStore.loadNotifications();
  connectGlobalWs();
}

async function checkAuth() {
  authState.value = 'booting';
  authErrorMessage.value = '';

  try {
    await loadAuthenticatedSession();
    authState.value = 'authenticated';
  } catch (error) {
    disconnectGlobalWs();
    currentUser.value = null;
    const normalized = normalizeApiError(error);
    authState.value = classifyAuthError(normalized);
    if (authState.value === 'error') {
      authErrorMessage.value = normalized.network
        ? '暂时无法连接 FlyMail，请检查网络后重试'
        : normalized.detail || normalized.message || 'FlyMail 暂时不可用，请稍后重试';
    }
  }
}

async function handleLoginSuccess() {
  await checkAuth();
}

function toggleNotifications() {
  mobileSidebarOpen.value = false;
  showNotifications.value = !showNotifications.value;
}

async function logout() {
  await api.post('/auth/logout');
  disconnectGlobalWs();
  currentUser.value = null;
  authState.value = 'anonymous';
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
  if (!payload?.account_id) return;
  nextTick(() => {
    window.dispatchEvent(new CustomEvent('flymail-sent-message', { detail: payload }));
  });
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
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
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
  disconnectGlobalWs();
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
  if (value !== 'compose') sessionStorage.setItem('flymail_view', value);
});
</script>

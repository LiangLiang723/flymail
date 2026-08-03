<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, onErrorCaptured, onMounted, ref, watch } from 'vue';
import { RouterView, useRoute, useRouter } from 'vue-router';

import AppIcon from '../components/AppIcon.vue';
import V2AppSidebar from '../components/app/V2AppSidebar.vue';
import MobileNavigationDrawer from '../features/navigation/MobileNavigationDrawer.vue';
import NavigationPanel from '../features/navigation/NavigationPanel.vue';
import { toNavigationAccounts } from '../features/navigation/navigation-state.ts';
import { useAuthState } from '../features/auth/auth-state.ts';
import { threadCursorMemory } from '../features/threads/thread-query.ts';
import DesktopMailLayout from '../layouts/DesktopMailLayout.vue';
import MobileMailLayout from '../layouts/MobileMailLayout.vue';
import TabletMailLayout from '../layouts/TabletMailLayout.vue';
import LoginPage from '../features/auth/LoginPage.vue';
import { apiClient, queryCache } from '../shared/api/client.ts';
import { normalizeApiError } from '../shared/api/errors.ts';
import type { BootstrapResponse, ThreadProjection } from '../shared/api/generated.ts';
import { RealtimeClient } from '../shared/realtime/client.ts';
import { shouldHandleShortcut } from '../shared/accessibility/focus.ts';
import { applyAppearance } from './appearance.ts';
import { useBootstrap } from './bootstrap.ts';
import { createErrorBoundaryState } from './error-boundary.ts';
import { layoutForWidth } from './router.ts';

const bootstrap = useBootstrap();
const auth = useAuthState();
const route = useRoute();
const router = useRouter();
const ThreadDetail = defineAsyncComponent(() => import('../features/message-viewer/ThreadDetail.vue'));
const viewportWidth = ref(typeof window === 'undefined' ? 1200 : window.innerWidth);
const boundary = createErrorBoundaryState(async () => {
  await router.replace(router.currentRoute.value.fullPath);
});
const layouts = { desktop: DesktopMailLayout, tablet: TabletMailLayout, mobile: MobileMailLayout };
const layoutMode = computed(() => layoutForWidth(viewportWidth.value));
const activeLayout = computed(() => layouts[layoutMode.value]);
const isMobileSidebar = computed(() => viewportWidth.value <= 960);
const isMailRoute = computed(() => route.name === 'mail');
const currentRoute = computed(() => {
  const name = String(route.name || 'mail');
  return name === 'notification-settings' ? 'notifications' : name;
});
const navigationAccounts = computed(() => toNavigationAccounts(
  bootstrap.state.data?.accounts || [],
  bootstrap.state.data?.navigation.accounts || [],
));
const selectedThreadId = computed(() => typeof route.query.thread === 'string' ? route.query.thread : '');
const expandedAccountIds = computed(() => {
  const value = bootstrap.state.data?.ui_preferences.expanded_account_ids;
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
});
const mobileDrawerOpen = ref(false);
const mobileSidebarOpen = ref(false);
const mobileNavigationButton = ref<HTMLElement | null>(null);
const sidebarCollapsed = ref(false);
const availableVersion = ref('');
const appVersion = computed(() => bootstrap.state.data?.version || import.meta.env.VITE_APP_VERSION || '0.0.0');
let realtimeClient: RealtimeClient | undefined;

function updateViewport() {
  viewportWidth.value = window.innerWidth;
  if (layoutMode.value !== 'mobile') mobileDrawerOpen.value = false;
  if (!isMobileSidebar.value) mobileSidebarOpen.value = false;
}

function handleGlobalShortcut(event: KeyboardEvent) {
  if (!shouldHandleShortcut(event)) return;
  if (event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey) {
    event.preventDefault();
    void router.push({ name: 'search' });
  }
  if (event.key === 'Escape') {
    mobileDrawerOpen.value = false;
    mobileSidebarOpen.value = false;
  }
}

async function saveNavigationPreference(value: { expanded_account_ids: string[] }) {
  const current = bootstrap.state.data?.ui_preferences || { theme: 'system', density: 'comfortable', expanded_account_ids: [] };
  await apiClient.request({
    method: 'PUT',
    path: '/api/v2/settings',
    body: { ui_preferences: { ...current, ...value } },
  });
  await bootstrap.load(true);
}

function handleAccountAction(accountId: string, action: 'reauthorize' | 'enable' | 'verify') {
  void router.push({ name: 'settings', query: { account: accountId, action } });
}

function refreshApplication() {
  window.location.reload();
}

function toggleSidebar() {
  if (isMobileSidebar.value) {
    mobileSidebarOpen.value = !mobileSidebarOpen.value;
    return;
  }
  sidebarCollapsed.value = !sidebarCollapsed.value;
}

async function navigate(key: string) {
  mobileSidebarOpen.value = false;
  const destinations: Record<string, Parameters<typeof router.push>[0]> = {
    mail: { name: 'mail', params: { scope: 'semantic', key: 'inbox' } },
    compose: { name: 'compose' },
    search: { name: 'search' },
    contacts: { name: 'contacts' },
    accounts: { name: 'accounts' },
    sync: { name: 'sync' },
    notifications: { name: 'notifications' },
    'notification-settings': { name: 'notification-settings' },
    profile: { name: 'profile' },
    settings: { name: 'settings' },
    admin: { name: 'admin' },
    backup: { name: 'backup' },
    about: { name: 'about' },
  };
  await router.push(destinations[key] || destinations.mail);
}

async function logout() {
  realtimeClient?.destroy();
  await auth.logout();
  await router.replace('/login');
}

async function changePassword() {
  const currentPassword = window.prompt('请输入当前密码');
  if (!currentPassword) return;
  const newPassword = window.prompt('请输入新密码');
  if (!newPassword) return;
  try {
    await apiClient.request({
      method: 'POST',
      path: '/api/v2/auth/password',
      body: {
        current_password: currentPassword,
        new_password: newPassword,
        revoke_other_sessions: true,
      },
    });
    window.alert('密码已更新');
  } catch (value: unknown) {
    window.alert(normalizeApiError(value).message);
  }
}

function startRealtime(data: BootstrapResponse) {
  realtimeClient?.destroy();
  realtimeClient = new RealtimeClient({
    initialSequence: data.realtime_cursor,
    fetchBacklog: (after) => apiClient.request({ method: 'GET', path: '/api/v2/events', query: { after, limit: 500 } }),
    handlers: {
      patchThread: (threadId, projection) => threadCursorMemory.patch(threadId, (projection || {}) as Partial<ThreadProjection>),
      removeThread: (threadId) => threadCursorMemory.remove(threadId),
      invalidateThread: (threadId) => queryCache.invalidate(['thread', threadId]),
      invalidateBody: (messageId) => queryCache.invalidate(['body', messageId]),
      invalidateScopes: (scopes) => {
        const scopeSet = new Set(scopes);
        if (scopeSet.has('threads')) threadCursorMemory.clear();
        queryCache.invalidateWhere((key) => scopes.some((scope) => key.includes(`\"${scope}\"`)));
      },
      authExpired: () => {
        bootstrap.clear();
        void router.replace('/login');
      },
      versionChanged: (version) => { availableVersion.value = version; },
      statusFallback: () => { void apiClient.request({ method: 'GET', path: '/api/v2/sync' }).catch(() => undefined); },
    },
  });
  realtimeClient.connect(data.realtime_cursor);
}

const removeAuthListener = apiClient.onAuthExpired(() => {
  realtimeClient?.destroy();
  bootstrap.clear();
  void router.replace('/login');
});

onErrorCaptured((error) => {
  boundary.capture(error);
  return false;
});

watch(
  () => bootstrap.state.data?.ui_preferences,
  (preferences) => applyAppearance(preferences),
  { deep: true, immediate: true },
);

watch(
  () => route.fullPath,
  () => {
    mobileSidebarOpen.value = false;
    if (!isMailRoute.value) mobileDrawerOpen.value = false;
  },
);

onMounted(async () => {
  window.addEventListener('resize', updateViewport, { passive: true });
  window.addEventListener('keydown', handleGlobalShortcut);
  const data = await bootstrap.load();
  if (data) startRealtime(data);
  if (!data && bootstrap.state.phase === 'anonymous' && router.currentRoute.value.path !== '/login') {
    await router.replace('/login');
  }
});

onBeforeUnmount(() => {
  window.removeEventListener('resize', updateViewport);
  window.removeEventListener('keydown', handleGlobalShortcut);
  removeAuthListener();
  realtimeClient?.destroy();
});
</script>

<template>
  <div class="v2-app" :data-density="bootstrap.state.data?.ui_preferences.density || 'comfortable'">
    <div v-if="bootstrap.state.phase === 'checking'" class="v2-boot" role="status" aria-live="polite">
      <strong>正在安全连接 FlyMail…</strong>
    </div>

    <LoginPage v-else-if="bootstrap.state.phase === 'anonymous'" />

    <section v-else-if="bootstrap.state.phase !== 'authenticated'" class="v2-state-card" role="alert">
      <h1>暂时无法打开邮箱</h1>
      <p>{{ bootstrap.state.error?.message || '服务正在维护，请稍后重试。' }}</p>
      <button type="button" @click="bootstrap.load(true)">重试</button>
    </section>

    <section v-else-if="boundary.state.hasError" class="v2-state-card" role="alert">
      <h1>页面加载失败</h1>
      <p>{{ boundary.state.message }}</p>
      <button type="button" @click="boundary.retry">重试当前页面</button>
    </section>

    <div
      v-else
      class="app-shell"
      :class="{
        'sidebar-collapsed': sidebarCollapsed && !isMobileSidebar,
        'mobile-sidebar-open': mobileSidebarOpen,
      }"
    >
      <V2AppSidebar
        :collapsed="sidebarCollapsed"
        :mobile="isMobileSidebar"
        :mobile-open="mobileSidebarOpen"
        :current-route="currentRoute"
        :user="bootstrap.state.data?.user || null"
        :app-version="appVersion"
        :unread-notifications="bootstrap.state.data?.sync_alert_summary.unread_notifications || 0"
        @toggle-collapse="toggleSidebar"
        @close-mobile="mobileSidebarOpen = false"
        @navigate="navigate"
        @change-password="changePassword"
        @logout="logout"
      />

      <div class="main">
        <button
          v-if="isMobileSidebar && !mobileSidebarOpen"
          class="mobile-sidebar-launcher"
          type="button"
          title="打开导航"
          aria-label="打开导航"
          @click="toggleSidebar"
        >
          <AppIcon name="menu" :size="19" />
        </button>

        <aside v-if="availableVersion" class="v2-version-banner" role="status">
          <span>FlyMail {{ availableVersion }} 已可用。</span>
          <button type="button" @click="refreshApplication">安全刷新</button>
        </aside>

        <button
          v-if="isMailRoute && layoutMode === 'mobile'"
          ref="mobileNavigationButton"
          type="button"
          class="v2-mobile-navigation-trigger"
          aria-haspopup="dialog"
          :aria-expanded="mobileDrawerOpen"
          @click="mobileDrawerOpen = true"
        >
          邮箱导航
        </button>

        <MobileNavigationDrawer
          v-if="isMailRoute && layoutMode === 'mobile'"
          :open="mobileDrawerOpen"
          :accounts="navigationAccounts"
          :return-focus="mobileNavigationButton"
          @close="mobileDrawerOpen = false"
        />

        <main class="content" :class="`content-${currentRoute}`">
          <component v-if="isMailRoute" :is="activeLayout">
            <template #navigation>
              <NavigationPanel
                :accounts="navigationAccounts"
                :expanded-account-ids="expandedAccountIds"
                @preference="saveNavigationPreference"
                @account-action="handleAccountAction"
              />
            </template>
            <template #default>
              <ThreadDetail v-if="selectedThreadId && layoutMode !== 'desktop'" :thread-id="selectedThreadId" />
              <RouterView v-else />
            </template>
            <template #list><RouterView /></template>
            <template #detail>
              <ThreadDetail v-if="selectedThreadId" :thread-id="selectedThreadId" />
              <div v-else class="v2-detail-empty">选择一封会话查看详情</div>
            </template>
          </component>
          <RouterView v-else />
        </main>
      </div>
    </div>
  </div>
</template>

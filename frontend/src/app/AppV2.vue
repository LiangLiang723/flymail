<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, onErrorCaptured, onMounted, ref } from 'vue';
import { RouterView, useRoute, useRouter } from 'vue-router';

import NavigationPanel from '../features/navigation/NavigationPanel.vue';
import MobileNavigationDrawer from '../features/navigation/MobileNavigationDrawer.vue';
import { toNavigationAccounts } from '../features/navigation/navigation-state.ts';
import { threadCursorMemory } from '../features/threads/thread-query.ts';
import DesktopMailLayout from '../layouts/DesktopMailLayout.vue';
import TabletMailLayout from '../layouts/TabletMailLayout.vue';
import MobileMailLayout from '../layouts/MobileMailLayout.vue';
import LoginPage from '../features/auth/LoginPage.vue';
import { apiClient, queryCache } from '../shared/api/client.ts';
import type { BootstrapResponse, ThreadProjection } from '../shared/api/generated.ts';
import { RealtimeClient } from '../shared/realtime/client.ts';
import { useBootstrap } from './bootstrap.ts';
import { createErrorBoundaryState } from './error-boundary.ts';
import { layoutForWidth } from './router.ts';

const bootstrap = useBootstrap();
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
const navigationAccounts = computed(() => toNavigationAccounts(bootstrap.state.data?.accounts || []));
const selectedThreadId = computed(() => typeof route.query.thread === 'string' ? route.query.thread : '');
const expandedAccountIds = computed(() => {
  const value = bootstrap.state.data?.preferences.expanded_account_ids;
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
});
const mobileDrawerOpen = ref(false);
const mobileNavigationButton = ref<HTMLElement | null>(null);
const availableVersion = ref('');
let realtimeClient: RealtimeClient | undefined;

function updateViewport() {
  viewportWidth.value = window.innerWidth;
  if (layoutMode.value !== 'mobile') mobileDrawerOpen.value = false;
}

async function saveNavigationPreference(value: { expanded_account_ids: string[] }) {
  const current = bootstrap.state.data?.preferences || {};
  await apiClient.request({
    method: 'PUT',
    path: '/api/v2/settings',
    body: { ui_preferences: { ...current, ...value } },
  });
}

function handleAccountAction(accountId: string, action: 'reauthorize' | 'enable' | 'verify') {
  void router.push({ name: 'settings', query: { account: accountId, action } });
}

function refreshApplication() {
  window.location.reload();
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

onMounted(async () => {
  window.addEventListener('resize', updateViewport, { passive: true });
  const data = await bootstrap.load();
  if (data) startRealtime(data);
  if (!data && bootstrap.state.phase === 'anonymous' && router.currentRoute.value.path !== '/login') {
    await router.replace('/login');
  }
});

onBeforeUnmount(() => {
  window.removeEventListener('resize', updateViewport);
  removeAuthListener();
  realtimeClient?.destroy();
});
</script>

<template>
  <div class="v2-app" :data-density="bootstrap.state.data?.preferences.density || 'comfortable'">
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

    <template v-else>
      <aside v-if="availableVersion" class="v2-version-banner" role="status">
        <span>FlyMail {{ availableVersion }} 已可用。</span>
        <button type="button" @click="refreshApplication">安全刷新</button>
      </aside>
      <button
        v-if="layoutMode === 'mobile'"
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
        :open="mobileDrawerOpen"
        :accounts="navigationAccounts"
        :return-focus="mobileNavigationButton"
        @close="mobileDrawerOpen = false"
      />
      <component :is="activeLayout">
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
    </template>
  </div>
</template>

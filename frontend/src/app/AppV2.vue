<script setup lang="ts">
import { computed, onBeforeUnmount, onErrorCaptured, onMounted, ref } from 'vue';
import { RouterView, useRouter } from 'vue-router';

import NavigationPanel from '../features/navigation/NavigationPanel.vue';
import MobileNavigationDrawer from '../features/navigation/MobileNavigationDrawer.vue';
import { toNavigationAccounts } from '../features/navigation/navigation-state.ts';
import DesktopMailLayout from '../layouts/DesktopMailLayout.vue';
import TabletMailLayout from '../layouts/TabletMailLayout.vue';
import MobileMailLayout from '../layouts/MobileMailLayout.vue';
import LoginPage from '../features/auth/LoginPage.vue';
import { apiClient } from '../shared/api/client.ts';
import { useBootstrap } from './bootstrap.ts';
import { createErrorBoundaryState } from './error-boundary.ts';
import { layoutForWidth } from './router.ts';

const bootstrap = useBootstrap();
const router = useRouter();
const viewportWidth = ref(typeof window === 'undefined' ? 1200 : window.innerWidth);
const boundary = createErrorBoundaryState(async () => {
  await router.replace(router.currentRoute.value.fullPath);
});
const layouts = { desktop: DesktopMailLayout, tablet: TabletMailLayout, mobile: MobileMailLayout };
const layoutMode = computed(() => layoutForWidth(viewportWidth.value));
const activeLayout = computed(() => layouts[layoutMode.value]);
const navigationAccounts = computed(() => toNavigationAccounts(bootstrap.state.data?.accounts || []));
const expandedAccountIds = computed(() => {
  const value = bootstrap.state.data?.preferences.expanded_account_ids;
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
});
const mobileDrawerOpen = ref(false);
const mobileNavigationButton = ref<HTMLElement | null>(null);

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
const removeAuthListener = apiClient.onAuthExpired(() => {
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
  if (!data && bootstrap.state.phase === 'anonymous' && router.currentRoute.value.path !== '/login') {
    await router.replace('/login');
  }
});

onBeforeUnmount(() => {
  window.removeEventListener('resize', updateViewport);
  removeAuthListener();
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
        <template #default><RouterView /></template>
        <template #list><RouterView /></template>
        <template #detail><div class="v2-detail-empty">选择一封会话查看详情</div></template>
      </component>
    </template>
  </div>
</template>

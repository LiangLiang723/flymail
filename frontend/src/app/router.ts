import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router';

import { bootstrapController } from './bootstrap.ts';

export type LayoutMode = 'mobile' | 'tablet' | 'desktop';

export function layoutForWidth(width: number): LayoutMode {
  if (width < 768) return 'mobile';
  if (width < 1200) return 'tablet';
  return 'desktop';
}

export const routeRecords: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: () => import('../features/auth/LoginPage.vue') },
  { path: '/', redirect: '/mail/inbox' },
  { path: '/mail/:scope?/:key?', name: 'mail', component: () => import('../features/threads/ThreadListPage.vue') },
  { path: '/search', name: 'search', component: () => import('../features/search/SearchPage.vue') },
  { path: '/compose/:draftId?', name: 'compose', component: () => import('../features/compose/ComposePage.vue') },
  { path: '/settings', name: 'settings', component: () => import('../features/settings/SettingsPage.vue') },
  { path: '/sync', name: 'sync', component: () => import('../features/sync-center/SyncCenterPage.vue') },
  { path: '/admin', name: 'admin', meta: { requiresAdmin: true }, component: () => import('../features/admin/AdminPage.vue') },
  { path: '/backup', name: 'backup', meta: { requiresAdmin: true }, component: () => import('../features/backup/BackupPage.vue') },
  { path: '/about', name: 'about', component: () => import('../features/about/AboutPage.vue') },
  { path: '/:pathMatch(.*)*', redirect: '/mail/inbox' },
];

export function createV2Router() {
  const router = createRouter({
    history: createWebHistory(import.meta.env.BASE_URL),
    routes: routeRecords,
    scrollBehavior: () => ({ top: 0 }),
  });
  router.beforeEach(async (to) => {
    if (to.name === 'login') return true;
    const data = await bootstrapController.load();
    if (!data) return { name: 'login' };
    if (to.meta.requiresAdmin && data.user.role !== 'admin') return { name: 'mail', params: { scope: 'semantic', key: 'inbox' } };
    return true;
  });
  return router;
}

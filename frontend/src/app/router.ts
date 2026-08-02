import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router';

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
  { path: '/search', name: 'search', component: () => import('./route-placeholders.ts').then((module) => module.SearchPlaceholder) },
  { path: '/compose/:draftId?', name: 'compose', component: () => import('../features/compose/ComposePage.vue') },
  { path: '/settings', name: 'settings', component: () => import('./route-placeholders.ts').then((module) => module.SettingsPlaceholder) },
  { path: '/sync', name: 'sync', component: () => import('./route-placeholders.ts').then((module) => module.SyncPlaceholder) },
  { path: '/admin', name: 'admin', component: () => import('./route-placeholders.ts').then((module) => module.AdminPlaceholder) },
  { path: '/backup', name: 'backup', component: () => import('./route-placeholders.ts').then((module) => module.BackupPlaceholder) },
  { path: '/about', name: 'about', component: () => import('./route-placeholders.ts').then((module) => module.AboutPlaceholder) },
  { path: '/:pathMatch(.*)*', redirect: '/mail/inbox' },
];

export function createV2Router() {
  return createRouter({
    history: createWebHistory(import.meta.env.BASE_URL),
    routes: routeRecords,
    scrollBehavior: () => ({ top: 0 }),
  });
}

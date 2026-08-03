import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

async function read(path: string) {
  return readFile(new URL(`../../${path}`, import.meta.url), 'utf8');
}

test('formal V2 entry loads the complete V1 design system before V2 compatibility styles', async () => {
  const main = await read('src/main.ts');
  const ordered = [
    './styles/tokens.css',
    './styles/base.css',
    './styles/components.css',
    './styles/app-shell.css',
    './styles/layout-system.css',
    './styles/page-system.css',
    './styles/v2-tokens.css',
    './styles/v2-base.css',
    './styles/v2-layout.css',
    './styles/v1-v2-compat.css',
  ];

  let lastIndex = -1;
  for (const stylesheet of ordered) {
    const index = main.indexOf(stylesheet);
    assert.ok(index > lastIndex, `${stylesheet} should be imported in restoration order`);
    lastIndex = index;
  }
  assert.match(main, /createApp\(AppV2\)/);
});

test('authenticated V2 app uses the V1 shell and limits the mail panes to mail routes', async () => {
  const app = await read('src/app/AppV2.vue');
  const sidebar = await read('src/components/app/V2AppSidebar.vue');

  assert.match(app, /import V2AppSidebar/);
  assert.match(app, /class="app-shell"/);
  assert.match(app, /class="main"/);
  assert.match(app, /class="content/);
  assert.match(app, /const isMailRoute = computed/);
  assert.match(app, /<component\s+v-if="isMailRoute"\s+:is="activeLayout"/);
  assert.match(app, /<RouterView v-else/);

  assert.match(sidebar, /class="app-sidebar"/);
  assert.match(sidebar, /sidebar-collapsed-toggle/);
  assert.match(sidebar, /sidebar-brand-logo/);
  assert.match(sidebar, /nav-list/);
  assert.match(sidebar, /sidebar-bottom/);
  assert.match(sidebar, /\/icon\.png/);
});

test('V2 theme preference drives both V2 data attributes and the V1 dark class', async () => {
  const appearance = await read('src/app/appearance.ts');
  assert.match(appearance, /classList\.toggle\('dark'/);
  assert.match(appearance, /matchMedia\('\(prefers-color-scheme: dark\)'\)/);
});

test('V2 login keeps V2 authentication while restoring V1 product primitives', async () => {
  const login = await read('src/features/auth/LoginPage.vue');
  assert.match(login, /useAuthState/);
  assert.match(login, /<UiCard/);
  assert.match(login, /<UiField/);
  assert.match(login, /<UiButton/);
  assert.match(login, /<UiAlert/);
  assert.match(login, /欢迎回来/);
  assert.doesNotMatch(login, /FlyMail V2/);
});

test('compatibility stylesheet covers every V2 feature surface and dialog family', async () => {
  const style = await read('src/styles/v1-v2-compat.css');
  for (const selector of [
    '.v2-about-page',
    '.v2-accounts-page',
    '.v2-admin-page',
    '.v2-backup-page',
    '.v2-compose-page',
    '.v2-contacts-page',
    '.v2-notification-center',
    '.v2-notification-settings',
    '.v2-profile-page',
    '.v2-search-page',
    '.v2-settings-page',
    '.v2-sync-page',
    '.v2-account-wizard',
    '.v2-draft-conflict',
    '.v2-schedule-dialog',
    '.v2-server-path-picker',
    '.v2-notification-detail',
    '.v2-avatar-crop',
    '.v2-advanced-filters',
  ]) {
    assert.match(style, new RegExp(selector.replace('.', '\\.')));
  }
});

import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

async function readSource(relativePath) {
  return readFile(path.join(frontendRoot, relativePath), 'utf8');
}

test('authentication boot state never renders the login form before session checking finishes', async () => {
  const appSource = await readSource('src/App.vue');
  const loginSource = await readSource('src/views/LoginView.vue');

  assert.match(appSource, /<AuthGate/);
  assert.doesNotMatch(appSource, /!authReady\s*\|\|\s*!currentUser/);
  assert.match(appSource, /authState/);
  assert.match(loginSource, /role="alert"/);
  assert.match(loginSource, /getLoginErrorMessage/);
});

test('application shell uses extracted navigation components without legacy duplicate markup', async () => {
  const appSource = await readSource('src/App.vue');
  const sidebarSource = await readSource('src/components/app/AppSidebar.vue');
  const userMenuSource = await readSource('src/components/app/UserMenu.vue');
  const shellCss = await readSource('src/styles/app-shell.css');

  assert.match(appSource, /<AppSidebar/);
  assert.match(appSource, /<NotificationDrawer/);
  assert.doesNotMatch(appSource, /v-if="false"/);
  assert.doesNotMatch(appSource, /<style scoped>/);
  assert.doesNotMatch(appSource, /class="topbar"/);
  assert.match(sidebarSource, /class="sidebar-header sidebar-row"/);
  assert.match(sidebarSource, /class="nav-list"/);
  assert.match(sidebarSource, /v-for="item in navItems"/);
  assert.doesNotMatch(sidebarSource, /class="nav-group-label"/);
  assert.match(userMenuSource, /class="sidebar-profile-trigger"/);
  assert.match(userMenuSource, /class="user-menu-popover"/);
  assert.match(shellCss, /\.user-menu-popover\s*\{[^}]*position:\s*fixed/s);
});

test('desktop application shell is a viewport grid instead of stacking the sidebar above content', async () => {
  const shellCss = await readSource('src/styles/app-shell.css');

  assert.match(shellCss, /\.app-shell\s*\{[^}]*display:\s*grid;/s);
  assert.match(shellCss, /\.app-shell\s*\{[^}]*height:\s*100(?:d)?vh;/s);
  assert.match(shellCss, /\.app-shell\s*\{[^}]*grid-template-rows:\s*minmax\(0,\s*1fr\)/s);
  assert.match(shellCss, /\.app-shell\s*\{[^}]*overflow:\s*hidden;/s);
  assert.match(shellCss, /\.app-sidebar\s*\{[^}]*grid-column:\s*1;[^}]*grid-row:\s*1;/s);
  assert.match(shellCss, /\.main\s*\{[^}]*grid-column:\s*2;[^}]*grid-row:\s*1;/s);
  assert.match(shellCss, /\.toast-container\s*\{[^}]*position:\s*fixed;/s);
  assert.match(shellCss, /\.notification-overlay,\s*\.confirm-overlay\s*\{[^}]*position:\s*fixed;/s);
  assert.match(shellCss, /@media \(max-width:\s*960px\)[\s\S]*\.main\s*\{[^}]*grid-column:\s*1;/s);
});

test('application overlays keep dense layout and independent scrolling', async () => {
  const shellCss = await readSource('src/styles/app-shell.css');

  assert.match(shellCss, /\.notification-drawer\s*\{[^}]*display:\s*flex;[^}]*flex-direction:\s*column;/s);
  assert.match(shellCss, /\.notification-list\s*\{[^}]*flex:\s*1;[^}]*min-height:\s*0;[^}]*overflow-y:\s*auto;/s);
  assert.match(shellCss, /\.notification-item\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:/s);
  assert.match(shellCss, /\.toast-container \.toast-item\s*\{[^}]*padding:/s);
  assert.match(shellCss, /\.confirm-dialog\s*\{[^}]*padding:/s);
  assert.match(shellCss, /\.confirm-actions\s*\{[^}]*display:\s*flex;/s);
});

test('mail view keeps the toolbar inside the list card without a permanent preview pane', async () => {
  const source = await readSource('src/views/MailList.vue');
  const listStart = source.indexOf('class="mail-list"');
  const toolbarStart = source.indexOf('class="list-toolbar"');

  assert.match(source, /class="folder-sidebar-header"/);
  assert.match(source, /class="account-switcher"/);
  assert.ok(listStart >= 0, 'mail list container should exist');
  assert.ok(toolbarStart > listStart, 'list toolbar should stay inside the mail list container');
  assert.doesNotMatch(source, /mail-preview-pane/);
});

test('responsive shell keeps a stable 72px icon rail and uses a mobile drawer', async () => {
  const appSource = await readSource('src/App.vue');
  const sidebarSource = await readSource('src/components/app/AppSidebar.vue');
  const shellCss = await readSource('src/styles/app-shell.css');

  assert.match(appSource, /<AppSidebar/);
  assert.match(sidebarSource, /class="sidebar-icon-rail"/);
  assert.match(sidebarSource, /class="sidebar-label-pane"/);
  assert.match(sidebarSource, /class="mobile-sidebar-backdrop"/);
  assert.match(appSource, /flymail_sidebar_collapsed/);
  assert.match(shellCss, /--app-sidebar-expanded:\s*248px/);
  assert.match(shellCss, /--app-sidebar-collapsed:\s*72px/);
  assert.match(shellCss, /grid-template-columns:\s*72px minmax\(0,\s*1fr\)/);
  assert.doesNotMatch(shellCss, /\.app-shell\.sidebar-collapsed[^\{]*\{[^}]*flex-direction:/s);
  assert.match(sidebarSource, /class="mobile-mail-navigation"/);
  assert.match(sidebarSource, /type: 'reauth'/);
  assert.match(shellCss, /prefers-reduced-transparency/);
});

test('mobile mail view delegates account and folder navigation without horizontal overflow', async () => {
  const source = await readSource('src/views/MailList.vue');

  assert.doesNotMatch(source, /mobile-account-tabs/);
  assert.match(source, /flymail-mail-navigation/);
  assert.match(source, /function handleMailNavigation/);
  assert.match(source, /detail\.type === 'reauth'/);
  assert.match(source, /@media \(max-width: 768px\)[\s\S]*\.mail-item,[\s\S]*min-width: 0;/);
});

test('manual refresh animates only while the latest page request is active', async () => {
  const source = await readSource('src/views/MailList.vue');

  assert.match(source, /class="btn-icon refresh-button"/);
  assert.match(source, /:class="\{ 'is-refreshing': refreshingLatest \}"/);
  assert.match(source, /const refreshingLatest = ref\(false\)/);
  assert.match(source, /refreshingLatest\.value = true/);
  assert.match(source, /finally \{\s*refreshingLatest\.value = false;/s);
  assert.match(source, /\.refresh-button\.is-refreshing svg\s*\{[^}]*animation: spin 0\.8s linear infinite;/s);
  assert.match(source, /@media \(prefers-reduced-motion: reduce\)[\s\S]*\.refresh-button\.is-refreshing svg\s*\{\s*animation: none;/);
});

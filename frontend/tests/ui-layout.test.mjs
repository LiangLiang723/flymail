import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

async function readSource(relativePath) {
  return readFile(path.join(frontendRoot, relativePath), 'utf8');
}

test('application shell uses one flat sidebar and keeps global actions out of a topbar', async () => {
  const source = await readSource('src/App.vue');

  assert.match(source, /class="sidebar-header"/);
  assert.match(source, /class="nav-list"/);
  assert.match(source, /v-for="item in navItems"/);
  assert.doesNotMatch(source, /class="nav-group-label"/);
  assert.doesNotMatch(source, /class="topbar"/);
  assert.match(source, /class="sidebar-actions"/);
  assert.match(source, /class="sidebar-profile-trigger"/);
  assert.match(source, /class="user-menu-popover"/);
  assert.match(source, /\.user-menu-popover\s*\{[^}]*position:\s*fixed;/s);
  assert.match(source, /<AppIcon/);
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

test('responsive shell keeps a desktop icon rail and uses a mobile drawer', async () => {
  const source = await readSource('src/App.vue');

  assert.match(source, /class="sidebar-toggle"/);
  assert.match(source, /class="mobile-sidebar-launcher"/);
  assert.match(source, /class="mobile-sidebar-backdrop"/);
  assert.match(source, /flymail_sidebar_collapsed/);
  assert.match(source, /\.app-shell\.sidebar-collapsed\s*\{[^}]*grid-template-columns:\s*68px\s+minmax\(0,\s*1fr\)/s);
  assert.match(source, /\.app-shell\.sidebar-collapsed \.nav-item-label[\s\S]*display:\s*none/);
  assert.doesNotMatch(source, /\.app-shell\.sidebar-collapsed \.sidebar\s*\{\s*opacity:\s*0;[^}]*pointer-events:\s*none/s);
  assert.match(source, /class="mobile-mail-navigation"/);
  assert.match(source, /new CustomEvent\('flymail-mail-navigation'/);
  assert.match(source, /type: 'reauth'/);
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

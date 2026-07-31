import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (file) => readFile(path.join(root, file), 'utf8');

test('shared page patterns own reusable controls and surfaces', async () => {
  const components = await read('src/styles/components.css');
  const pages = await read('src/styles/page-system.css');

  for (const selector of ['.ui-input', '.ui-select', '.ui-textarea', '.ui-checkbox', '.ui-badge', '.ui-segmented']) {
    assert.match(components, new RegExp(selector.replace('.', '\\.')));
  }
  for (const selector of ['.ui-section', '.ui-stat-grid', '.ui-list-row', '.ui-detail-grid']) {
    assert.match(pages, new RegExp(selector.replace('.', '\\.')));
  }
});

test('sidebar header renders mutually exclusive desktop controls', async () => {
  const source = await read('src/components/app/AppSidebar.vue');

  assert.match(source, /v-if="collapsed && !mobile"[\s\S]*class="sidebar-collapsed-toggle"/);
  assert.match(source, /v-if="!mobile"[\s\S]*class="sidebar-header-action"/);
  assert.match(source, /v-else[\s\S]*class="sidebar-mobile-close"/);
  assert.doesNotMatch(source, /class="sidebar-header sidebar-row"/);
});

test('management consoles use fluid responsive product layouts', async () => {
  const contracts = {
    'AccountList.vue': ['account-card-grid', 'UiSegmentedControl', 'UiBadge'],
    'HistorySync.vue': ['sync-summary-grid', 'sync-card-grid', 'UiBadge'],
    'UserManagement.vue': ['user-list', 'UiField', 'UiBadge'],
  };

  for (const [file, required] of Object.entries(contracts)) {
    const source = await read(`src/views/${file}`);
    assert.match(source, /<PageFrame[^>]*width="fluid"/);
    for (const value of required) assert.match(source, new RegExp(value));
  }
});

test('unified inbox is a fluid workspace built from shared sections', async () => {
  const source = await read('src/views/UnifiedInbox.vue');

  assert.match(source, /<PageFrame[^>]*template="management"[^>]*width="fluid"/);
  assert.match(source, /<UiCard/);
  assert.match(source, /<UiSegmentedControl/);
  assert.match(source, /class="unified-account-layout"/);
  assert.match(source, /class="ui-list-row message-row"/);
});

test('anonymous and global floating surfaces use shared product primitives', async () => {
  const login = await read('src/views/LoginView.vue');
  const boot = await read('src/components/app/AppBootScreen.vue');
  const app = await read('src/App.vue');
  const viewer = await read('src/components/mail/ImageViewer.vue');

  assert.match(login, /<UiCard/);
  assert.match(login, /<UiField/);
  assert.match(login, /<UiButton/);
  assert.match(login, /<UiAlert/);
  assert.match(boot, /<UiCard/);
  assert.match(boot, /<UiSpinner/);
  assert.match(boot, /<UiButton/);
  assert.match(app, /confirm-dialog/);
  assert.match(viewer, /UiIconButton/);
});

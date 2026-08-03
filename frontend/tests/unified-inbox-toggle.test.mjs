import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

async function readSource(relativePath) {
  return readFile(path.join(frontendRoot, relativePath), 'utf8');
}

test('user menu keeps the profile trigger without a dropdown chevron', async () => {
  const source = await readSource('src/components/app/UserMenu.vue');

  assert.match(source, /class="sidebar-profile-trigger"/);
  assert.doesNotMatch(source, /profile-chevron/);
  assert.doesNotMatch(source, /name="chevron-down"/);
});

test('unified inbox navigation is disabled until the saved user preference enables it', async () => {
  const source = await readSource('src/App.vue');

  assert.match(source, /const unifiedInboxEnabled = ref\(false\)/);
  assert.match(source, /unifiedInboxEnabled\.value\s*\?\s*\[\{ key: 'unified'/s);
  assert.match(source, /api\.get\('\/settings\/unified'\)/);
  assert.match(source, /flymail-unified-inbox-setting-changed/);
  assert.match(source, /currentView\.value === 'unified'/);
});

test('settings page exposes an accessible unified inbox toggle and saves it separately', async () => {
  const source = await readSource('src/views/Settings.vue');

  assert.match(source, /聚合收件箱/);
  assert.match(source, /class="settings-toggle-switch"/);
  assert.match(source, /aria-label="启用聚合收件箱"/);
  assert.match(source, /:aria-pressed="unifiedInboxEnabled"/);
  assert.match(source, /api\.put\('\/settings\/unified', \{ enabled: unifiedInboxEnabled\.value \}\)/);
  assert.match(source, /flymail-unified-inbox-setting-changed/);
});

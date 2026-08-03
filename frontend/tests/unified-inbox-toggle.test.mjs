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

test('settings page exposes a native unified inbox switch with touch and async feedback', async () => {
  const source = await readSource('src/views/Settings.vue');

  assert.match(source, /聚合收件箱/);
  assert.match(source, /class="settings-toggle-input"/);
  assert.match(source, /type="checkbox"/);
  assert.match(source, /role="switch"/);
  assert.match(source, /:checked="unifiedInboxEnabled"/);
  assert.match(source, /aria-labelledby="unified-inbox-title"/);
  assert.match(source, /aria-describedby="unified-inbox-description unified-inbox-feedback"/);
  assert.match(source, /aria-live="polite"/);
  assert.doesNotMatch(source, /aria-pressed/);
  assert.match(source, /\.settings-toggle-control\s*\{[^}]*min-height:\s*var\(--touch-target\);/s);
  assert.match(source, /const nextEnabled = input\.checked/);
  assert.match(source, /api\.put\('\/settings\/unified', \{ enabled: nextEnabled \}\)/);
  assert.match(source, /flymail-unified-inbox-setting-changed/);
});

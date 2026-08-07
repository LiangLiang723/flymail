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

test('unified inbox supports local keyword search without changing selected account scope', async () => {
  const source = await readSource('src/views/UnifiedInbox.vue');

  assert.match(source, /v-model\.trim="searchKeyword"/);
  assert.match(source, /placeholder="搜索主题\/发件人\/正文"/);
  assert.match(source, /if \(searchKeyword\.value\) params\.keyword = searchKeyword\.value/);
  assert.match(source, /function clearSearch\(\)/);
});

test('settings page uses the shared unified inbox switch with async feedback', async () => {
  const source = await readSource('src/views/Settings.vue');
  const switchSource = await readSource('src/components/ui/UiSwitch.vue');
  const styles = await readSource('src/styles/components.css');

  assert.match(source, /聚合收件箱/);
  assert.match(source, /<UiSwitch[\s\S]*:model-value="unifiedInboxEnabled"/);
  assert.match(source, /described-by="unified-inbox-description unified-inbox-feedback"/);
  assert.match(source, /@update:model-value="toggleUnifiedInbox"/);
  assert.match(source, /aria-live="polite"/);
  assert.doesNotMatch(source, /aria-pressed/);
  assert.match(switchSource, /role="switch"/);
  assert.match(styles, /\.ui-switch\s*\{[^}]*min-height:\s*var\(--touch-target\);/s);
  assert.match(source, /async function toggleUnifiedInbox\(nextEnabled: boolean\)/);
  assert.match(source, /api\.put\('\/settings\/unified', \{ enabled: nextEnabled \}\)/);
  assert.match(source, /flymail-unified-inbox-setting-changed/);
});

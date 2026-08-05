import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const readSource = (relativePath) => readFile(path.join(frontendRoot, relativePath), 'utf8');

test('app registers signature management without adding a primary sidebar item', async () => {
  const app = await readSource('src/App.vue');

  assert.match(app, /import SignatureManagement from '\.\/views\/SignatureManagement\.vue'/);
  assert.match(app, /currentView === 'signatures'/);
  assert.match(app, /@back="returnFromSignatureManagement"/);
  assert.match(app, /menuViews = \['profile', 'notifications', 'settings', 'signatures', 'about'\]/);
  assert.doesNotMatch(app, /navItems[\s\S]{0,500}key:\s*'signatures'/);
});

test('all signature exits use the shared unsaved confirmation gate', async () => {
  const app = await readSource('src/App.vue');

  assert.match(app, /async function requestNavigation/);
  assert.match(app, /signatureStore\.hasUnsavedChanges/);
  assert.match(app, /uiStore\.showConfirm/);
  assert.match(app, /signatureStore\.discardDraft\(\)/);
  assert.match(app, /async function returnFromSignatureManagement/);
});

test('settings and user menu expose signature management entry points', async () => {
  const settings = await readSource('src/views/Settings.vue');
  const userMenu = await readSource('src/components/app/UserMenu.vue');
  const icons = await readSource('src/components/AppIcon.vue');

  assert.match(settings, /邮件签名/);
  assert.match(settings, /管理签名/);
  assert.match(settings, /signatureStore\.signatureCount/);
  assert.match(settings, /signatureStore\.setEntrySource\('settings'\)/);
  assert.match(userMenu, /签名管理/);
  assert.match(userMenu, /navigate\('signatures'\)/);
  assert.match(icons, /name === 'signature'/);
});

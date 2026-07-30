import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const readSource = (relativePath) => readFile(path.join(root, relativePath), 'utf8');

test('account menu owns profile and management destinations while primary navigation stays focused', async () => {
  const app = await readSource('src/App.vue');
  const menu = await readSource('src/components/app/UserMenu.vue');
  const sidebar = await readSource('src/components/app/AppSidebar.vue');
  const profile = await readSource('src/views/Profile.vue');

  assert.match(app, /Profile v-else-if="currentView === 'profile'"/);
  assert.doesNotMatch(app, /\{ key: 'users', label: '用户管理'/);
  assert.doesNotMatch(app, /\{ key: 'settings', label: '设置'/);
  assert.match(menu, />个人资料</);
  assert.match(menu, />用户管理</);
  assert.match(menu, />第三方通知</);
  assert.match(menu, />设置</);
  assert.match(menu, />关于</);
  assert.match(menu, /avatar_url/);
  assert.match(sidebar, /@navigate=".*navigate/);
  assert.match(profile, /v-model\.trim="form\.username"/);
  assert.match(profile, /v-model\.trim="form\.nickname"/);
  assert.match(profile, /accept="image\/\*"/);
});

test('mail detail hides inline assets from attachment rows and opens a dedicated image viewer', async () => {
  const mail = await readSource('src/views/MailList.vue');
  const viewer = await readSource('src/components/mail/ImageViewer.vue');

  assert.match(mail, /regularAttachments/);
  assert.match(mail, /!attachment\.is_inline/);
  assert.match(mail, /<ImageViewer/);
  assert.match(mail, /handleMailBodyClick/);
  assert.match(viewer, /pointerdown/);
  assert.match(viewer, /pointermove/);
  assert.match(viewer, /touch-action:\s*none/);
  assert.match(viewer, /zoomIn/);
  assert.match(viewer, /showPrevious/);
  assert.match(viewer, /showNext/);
});

test('administrator user management offers profile editing', async () => {
  const source = await readSource('src/views/UserManagement.vue');
  assert.match(source, />编辑资料</);
  assert.match(source, /editForm\.username/);
  assert.match(source, /editForm\.nickname/);
  assert.match(source, /editAvatar/);
});

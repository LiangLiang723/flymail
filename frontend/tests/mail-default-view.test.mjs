import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const readSource = (relativePath) => readFile(path.join(frontendRoot, relativePath), 'utf8');

test('settings exposes and persists the default mail list view', async () => {
  const source = await readSource('src/views/Settings.vue');

  assert.match(source, />邮件列表默认显示</);
  assert.match(source, /default_mail_view/);
  assert.match(source, /value:\s*'messages'/);
  assert.match(source, /value:\s*'conversations'/);
  assert.match(source, /api\.put\('\/settings',\s*\{\s*default_mail_view:/s);
  assert.match(source, /flymail-default-mail-view-changed/);
  assert.match(source, />草稿箱始终按单封邮件显示</);
});

test('mail list loads the saved default before its first list request and keeps drafts in message mode', async () => {
  const source = await readSource('src/views/MailList.vue');

  assert.match(source, /const preferredListMode = ref<'messages' \| 'conversations'>\('messages'\)/);
  assert.match(source, /async function loadDefaultMailViewPreference\(\)/);
  assert.match(source, /api\.get\('\/settings'\)/);
  assert.match(source, /data\?\.default_mail_view === 'conversations'/);
  assert.match(source, /await loadDefaultMailViewPreference\(\);[\s\S]*await loadMessages\(\)/);
  assert.match(source, /isDraftFolder\.value \? 'messages' : preferredListMode\.value/);
  assert.match(source, /flymail-default-mail-view-changed/);
});

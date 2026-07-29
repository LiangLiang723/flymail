import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

async function readSource(relativePath) {
  return readFile(path.join(frontendRoot, relativePath), 'utf8');
}

test('application shell groups navigation and keeps account actions in a user menu', async () => {
  const source = await readSource('src/App.vue');

  assert.match(source, /class="nav-groups"/);
  assert.match(source, /class="nav-group-label"/);
  assert.match(source, /class="user-menu-trigger"/);
  assert.match(source, /class="user-menu-popover"/);
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

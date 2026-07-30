import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (file) => readFile(path.join(root, file), 'utf8');

test('the authenticated shell owns one complete viewport height chain', async () => {
  const base = await read('src/styles/base.css');
  const shell = await read('src/styles/app-shell.css');
  const app = await read('src/App.vue');

  assert.match(base, /html[\s\S]*body[\s\S]*#app[\s\S]*height:\s*100%/);
  assert.match(base, /body\s*\{[^}]*overflow:\s*hidden/s);
  assert.match(shell, /\.app-shell\s*\{[^}]*height:\s*100dvh/s);
  assert.match(shell, /\.main[\s\S]*\.content[\s\S]*height:\s*100%/);
  assert.match(app, /class="app-page-viewport"/);
});

test('the page viewport clips outer overflow and delegates scrolling to templates', async () => {
  const shell = await read('src/styles/app-shell.css');

  assert.match(shell, /\.app-page-viewport\s*\{[^}]*min-height:\s*0/s);
  assert.match(shell, /\.app-page-viewport\s*\{[^}]*overflow:\s*hidden/s);
});

test('page frames adapt their rows to optional headers and toolbars', async () => {
  const frame = await read('src/components/layout/PageFrame.vue');
  const layout = await read('src/styles/layout-system.css');

  assert.match(frame, /page-frame--has-header/);
  assert.match(frame, /page-frame--has-toolbar/);
  assert.match(layout, /\.page-frame\s*\{[^}]*grid-template-rows:\s*minmax\(0,\s*1fr\)/s);
  assert.match(layout, /\.page-frame--has-header\.page-frame--has-toolbar[^}]*grid-template-rows:\s*auto auto minmax\(0,\s*1fr\)/s);
});

test('each page template owns a deterministic scroll model', async () => {
  const layout = await read('src/styles/layout-system.css');

  assert.match(layout, /\.page-frame--management[^}]*overflow:\s*hidden/s);
  assert.match(layout, /\.page-frame--management[\s\S]*\.page-frame__body[^}]*overflow-y:\s*auto/s);
  assert.match(layout, /\.page-frame--workspace[^}]*overflow:\s*hidden/s);
  assert.match(layout, /\.page-frame--split[^}]*overflow:\s*hidden/s);
  assert.match(layout, /\.page-frame--document[\s\S]*\.page-frame__body[^}]*overflow-y:\s*auto/s);
});

test('mail workspace pages use the workspace template', async () => {
  for (const file of ['MailList.vue', 'ComposeEmail.vue', 'Backup.vue']) {
    const source = await read(`src/views/${file}`);
    assert.match(source, /<PageFrame[^>]*template="workspace"/);
  }
});

test('mail workspace roots do not declare page-level vertical scrolling', async () => {
  for (const file of ['MailList.vue', 'ComposeEmail.vue', 'Backup.vue']) {
    const source = await read(`src/views/${file}`);
    const styles = [...source.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)].map((match) => match[1]).join('\n');
    assert.doesNotMatch(styles, /\.(mail-view|compose-page|backup-page)[^{]*\{[^}]*overflow-y:\s*auto/s);
  }
});

test('management pages use the management template and shared header', async () => {
  for (const file of ['UnifiedInbox.vue', 'HistorySync.vue', 'AccountList.vue', 'UserManagement.vue']) {
    const source = await read(`src/views/${file}`);
    assert.match(source, /<PageFrame[^>]*template="management"/);
    assert.match(source, /<PageHeader/);
  }
});

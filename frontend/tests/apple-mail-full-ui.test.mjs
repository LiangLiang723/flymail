import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile, readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (file) => readFile(path.join(root, file), 'utf8');

async function collectVueFiles(relativeDir) {
  const directory = path.join(root, relativeDir);
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(entries.map(async (entry) => {
    const relativePath = path.join(relativeDir, entry.name);
    if (entry.isDirectory()) return collectVueFiles(relativePath);
    return entry.isFile() && entry.name.endsWith('.vue') ? [relativePath] : [];
  }));
  return nested.flat();
}

test('Apple Mail visual tokens use system blue and compact workspace measurements', async () => {
  const tokens = await read('src/styles/tokens.css');

  assert.match(tokens, /--ui-accent:\s*#0a84ff/i);
  assert.match(tokens, /--ui-accent-hover:\s*#0077e6/i);
  assert.match(tokens, /--page-gutter:\s*18px/);
  assert.match(tokens, /--toolbar-height:\s*44px/);
  assert.match(tokens, /--list-row-height:\s*52px/);
  assert.match(tokens, /--mail-folder-pane:\s*236px/);
  assert.match(tokens, /--mail-list-pane:\s*390px/);
});

test('desktop mail workspace renders list and detail together with an empty detail state', async () => {
  const mail = await read('src/views/MailList.vue');

  assert.match(mail, /v-show="!isMobile \|\| !selectedMessage" class="mail-list"/);
  assert.match(mail, /v-if="selectedMessage" class="mail-detail"/);
  assert.match(mail, /v-else-if="!isMobile" class="mail-detail mail-detail-empty"/);
  assert.match(mail, /title="选择一封邮件"/);
  assert.doesNotMatch(mail, /<div v-if="!selectedMessage" class="mail-list">/);
  assert.doesNotMatch(mail, /<div v-else class="mail-detail"/);
  assert.doesNotMatch(mail, /class="mail-status-tag"/);
});

test('mail workspace defines three-pane, two-pane and mobile breakpoints', async () => {
  const styles = await read('src/styles/page-system.css');

  assert.match(
    styles,
    /\.mail-view \.mail-shell\s*\{[^}]*grid-template-columns:\s*var\(--mail-folder-pane\) minmax\(320px,\s*var\(--mail-list-pane\)\) minmax\(0,\s*1fr\)/s,
  );
  assert.match(styles, /@media \(max-width:\s*1180px\) and \(min-width:\s*961px\)/);
  assert.match(styles, /@media \(max-width:\s*960px\)/);
  assert.match(styles, /\.mail-view \.mail-detail-empty/);
});

test('settings page exposes category navigation and semantic content sections', async () => {
  const settings = await read('src/views/Settings.vue');

  assert.match(settings, /class="settings-layout"/);
  assert.match(settings, /class="settings-nav"/);
  assert.match(settings, /aria-label="设置分类"/);
  for (const id of [
    'settings-appearance',
    'settings-storage',
    'settings-network',
    'settings-providers',
    'settings-guides',
  ]) {
    assert.match(settings, new RegExp(`id="${id}"`));
    assert.match(settings, new RegExp(`href="#${id}"`));
  }
  assert.doesNotMatch(settings, /style="[^"]*(?:rgba?\(|#[0-9a-f]{3,8})/i);
});

test('shared page system applies the Apple Mail treatment across every primary page family', async () => {
  const styles = await read('src/styles/page-system.css');
  const appShell = await read('src/styles/app-shell.css');

  for (const selector of [
    '.compose-page',
    '.contact-page',
    '.backup-page',
    '.unified-page',
    '.account-page',
    '.history-sync-page',
    '.user-page',
    '.settings-page',
    '.profile-page',
    '.notify-page',
    '.about-page',
  ]) {
    assert.match(styles, new RegExp(selector.replace('.', '\\.')));
  }
  assert.match(appShell, /touch-action:\s*manipulation/);
  assert.match(styles, /font-variant-numeric:\s*tabular-nums/);

  for (const file of await collectVueFiles('src/views')) {
    assert.doesNotMatch(await read(file), /transition:\s*all/i, `${file} must animate explicit properties`);
  }
  for (const file of await collectVueFiles('src/components')) {
    assert.doesNotMatch(await read(file), /transition:\s*all/i, `${file} must animate explicit properties`);
  }
});

test('README describes the true responsive mail workspace', async () => {
  const readme = await read('../README.md');

  assert.match(readme, /Apple Mail/);
  assert.match(readme, /系统蓝/);
  assert.match(readme, /三栏/);
  assert.match(readme, /双栏/);
  assert.match(readme, /单主视图/);
});

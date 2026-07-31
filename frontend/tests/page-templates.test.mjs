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

test('history sync distinguishes summary progress, active phase and failure time', async () => {
  const source = await read('src/views/HistorySync.vue');

  assert.match(source, /邮件摘要/);
  assert.match(source, /syncPhaseText\(item\)/);
  assert.match(source, /失败时间/);
  assert.match(source, /正在补全正文和附件/);
});

test('management pages use the management template and shared header', async () => {
  for (const file of ['UnifiedInbox.vue', 'HistorySync.vue', 'AccountList.vue', 'UserManagement.vue']) {
    const source = await read(`src/views/${file}`);
    assert.match(source, /<PageFrame[^>]*template="management"/);
    assert.match(source, /<PageHeader/);
  }
});

test('contacts use a full-height split template with two scroll owners', async () => {
  const source = await read('src/views/ContactList.vue');

  assert.match(source, /<PageFrame[^>]*template="split"[^>]*width="fluid"/);
  assert.match(source, /class="contact-workspace split-grid"/);
  assert.match(source, /class="contact-list-pane ui-scroll-region ui-scroll-region--y/);
  assert.match(source, /class="contact-detail-pane ui-scroll-region ui-scroll-region--y/);
  assert.match(source, /<UiEmptyState/);
});

test('settings documents use the document template and shared header', async () => {
  for (const file of ['Settings.vue', 'NotificationSettings.vue', 'About.vue', 'Profile.vue']) {
    const source = await read(`src/views/${file}`);
    assert.match(source, /<PageFrame[^>]*template="document"/);
    assert.match(source, /<PageHeader/);
    assert.match(source, /class="document-column/);
  }
});

test('desktop page templates share one outer content gutter', async () => {
  const layout = await read('src/styles/layout-system.css');

  assert.match(layout, /\.page-frame\s*\{[^}]*padding:\s*var\(--page-gutter\)/s);
  assert.match(layout, /@media \(max-width:\s*960px\)[\s\S]*\.page-frame--workspace,[\s\S]*\.page-frame--split\s*\{[^}]*padding:\s*0/s);
  assert.match(layout, /@media \(max-width:\s*960px\)[\s\S]*\.page-frame--management,[\s\S]*\.page-frame--document\s*\{[^}]*padding:\s*var\(--page-gutter-compact\)/s);
});

test('page frames expose explicit fluid form and reading widths', async () => {
  const frame = await read('src/components/layout/PageFrame.vue');
  const layout = await read('src/styles/layout-system.css');

  assert.match(frame, /type PageWidth = 'fluid' \| 'form' \| 'reading'/);
  assert.match(frame, /width\?: PageWidth/);
  assert.match(frame, /page-frame__shell/);
  assert.match(layout, /\.page-frame--width-fluid\s*\{[^}]*--page-frame-max:\s*none/s);
  assert.match(layout, /\.page-frame--width-form\s*\{[^}]*--page-frame-max:\s*var\(--page-form-max\)/s);
  assert.match(layout, /\.page-frame--width-reading\s*\{[^}]*--page-frame-max:\s*var\(--page-reading-max\)/s);
  assert.match(layout, /\.page-frame__shell\s*\{[^}]*max-width:\s*var\(--page-frame-max\);[^}]*margin-right:\s*auto/s);
});

test('split pages use a rounded desktop surface and edge-to-edge mobile layout', async () => {
  const layout = await read('src/styles/layout-system.css');

  assert.match(layout, /\.page-frame--split \.page-frame__body\s*\{[^}]*border:\s*1px solid var\(--ui-border\);[^}]*border-radius:\s*var\(--ui-radius-lg\);[^}]*background:\s*var\(--ui-surface-1\);[^}]*box-shadow:\s*var\(--ui-shadow-xs\)/s);
  assert.match(layout, /@media \(max-width:\s*960px\)[\s\S]*\.page-frame--split \.page-frame__body\s*\{[^}]*border:\s*0;[^}]*border-radius:\s*0;[^}]*box-shadow:\s*none/s);
});

test('shared empty state supports compact panel variants', async () => {
  const component = await read('src/components/ui/UiEmptyState.vue');
  const layout = await read('src/styles/layout-system.css');

  assert.match(component, /compact\?:\s*boolean/);
  assert.match(component, /panel\?:\s*boolean/);
  assert.match(component, /ui-empty-state--compact/);
  assert.match(component, /ui-empty-state--panel/);
  assert.match(layout, /\.ui-empty-state--compact\s*\{/);
  assert.match(layout, /\.ui-empty-state--panel\s*\{[^}]*border:\s*1px solid var\(--ui-border\)/s);
});

test('top-level data pages reuse the shared empty-state component', async () => {
  for (const file of ['AccountList.vue', 'ContactList.vue', 'HistorySync.vue', 'UnifiedInbox.vue', 'Backup.vue']) {
    const source = await read(`src/views/${file}`);
    assert.match(source, /import UiEmptyState from/);
    assert.match(source, /<UiEmptyState/);
  }
});

test('management toolbar pages use the shared PageToolbar structure', async () => {
  for (const file of ['AccountList.vue', 'UserManagement.vue']) {
    const source = await read(`src/views/${file}`);
    assert.match(source, /import PageToolbar from/);
    assert.match(source, /<PageToolbar/);
  }
});

test('top-level asynchronous pages reuse the shared loading-state component', async () => {
  const component = await read('src/components/ui/UiLoadingState.vue');
  assert.match(component, /ui-loading-state__spinner/);
  assert.match(component, /compact\?:\s*boolean/);
  assert.match(component, /panel\?:\s*boolean/);

  for (const file of ['AccountList.vue', 'ContactList.vue', 'HistorySync.vue', 'UnifiedInbox.vue', 'Backup.vue', 'NotificationSettings.vue']) {
    const source = await read(`src/views/${file}`);
    assert.match(source, /import UiLoadingState from/);
    assert.match(source, /<UiLoadingState/);
  }
});

test('page roots never override template-owned outer spacing', async () => {
  const files = [
    'MailList.vue',
    'ComposeEmail.vue',
    'Backup.vue',
    'UnifiedInbox.vue',
    'HistorySync.vue',
    'AccountList.vue',
    'UserManagement.vue',
    'ContactList.vue',
    'Settings.vue',
    'NotificationSettings.vue',
    'About.vue',
    'Profile.vue',
  ];
  const rootPattern = /\.(mail-view|compose-page|backup-page|unified-page|history-sync-page|account-page|user-page|contact-page|settings-page|notify-page|about-page|profile-page)\s*\{([^}]*)\}/g;

  for (const file of files) {
    const source = await read(`src/views/${file}`);
    const styles = [...source.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)].map((match) => match[1]).join('\n');
    for (const match of styles.matchAll(rootPattern)) {
      assert.doesNotMatch(match[2], /(?:^|[;\s])(padding|margin|overflow-y)\s*:/, `${file}: ${match[1]}`);
    }
  }
});

test('the compatibility layer no longer owns page root height, padding or scrolling', async () => {
  const source = await read('src/styles/page-system.css');
  const roots = new Set([
    '.mail-view',
    '.backup-page',
    '.compose-page',
    '.unified-page',
    '.history-sync-page',
    '.account-page',
    '.user-page',
    '.contact-page',
    '.settings-page',
    '.notify-page',
    '.about-page',
    '.ui-page:not(.ui-page--edge)',
  ]);

  for (const match of source.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selectors = match[1].split(',').map((selector) => selector.trim());
    if (!selectors.some((selector) => roots.has(selector))) continue;
    assert.doesNotMatch(match[2], /(?:^|[;\s])(height|min-height|padding|overflow|overflow-y)\s*:/, selectors.join(', '));
  }
});

import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile, readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

async function readSource(relativePath) {
  return readFile(path.join(frontendRoot, relativePath), 'utf8');
}

async function listVueFiles(relativeDirectory) {
  const directory = path.join(frontendRoot, relativeDirectory);
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const relativePath = path.join(relativeDirectory, entry.name);
    if (entry.isDirectory()) files.push(...await listVueFiles(relativePath));
    else if (entry.name.endsWith('.vue')) files.push(relativePath);
  }
  return files;
}

test('semantic tokens cover light, dark, spacing, depth and motion roles', async () => {
  const source = await readSource('src/styles/tokens.css');

  for (const token of [
    '--ui-canvas:',
    '--ui-surface-1:',
    '--ui-surface-floating:',
    '--ui-text-1:',
    '--ui-text-3:',
    '--ui-border:',
    '--ui-accent:',
    '--ui-success:',
    '--ui-warning:',
    '--ui-danger:',
    '--ui-focus-ring:',
    '--ui-motion-standard:',
  ]) {
    assert.match(source, new RegExp(token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  }
  assert.match(source, /:root\.dark\s*\{/);
  assert.match(source, /--app-sidebar-expanded:\s*248px/);
  assert.match(source, /--app-sidebar-collapsed:\s*72px/);
});

test('base styles include visible focus and accessibility preferences', async () => {
  const source = await readSource('src/styles/base.css');

  assert.match(source, /:focus-visible/);
  assert.match(source, /prefers-reduced-motion:\s*reduce/);
  assert.match(source, /prefers-reduced-transparency:\s*reduce/);
  assert.match(source, /prefers-contrast:\s*more/);
});

test('reusable button primitives expose variants and loading state', async () => {
  const button = await readSource('src/components/ui/UiButton.vue');
  const iconButton = await readSource('src/components/ui/UiIconButton.vue');

  assert.match(button, /'primary'\s*\|\s*'secondary'\s*\|\s*'ghost'\s*\|\s*'danger'/);
  assert.match(button, /loading/);
  assert.match(button, /UiSpinner/);
  assert.match(iconButton, /aria-label/);
  assert.match(iconButton, /label/);
});

test('shared button variants never style generic danger state classes', async () => {
  const source = await readSource('src/styles/components.css');

  assert.doesNotMatch(source, /(?:^|,)\s*\.danger\s*(?:,|\{)/m);
  assert.match(
    source,
    /\.ui-button--danger,[\s\S]*\.btn-danger,[\s\S]*\.toolbar-btn\.danger,[\s\S]*\.btn-delete\s*\{/,
  );
});

test('all authenticated pages opt into the shared page system', async () => {
  const pages = [
    'About.vue',
    'AccountList.vue',
    'Backup.vue',
    'ComposeEmail.vue',
    'ContactList.vue',
    'HistorySync.vue',
    'MailList.vue',
    'NotificationSettings.vue',
    'Settings.vue',
    'UnifiedInbox.vue',
    'UserManagement.vue',
  ];

  for (const page of pages) {
    const source = await readSource(`src/views/${page}`);
    assert.match(source, /class="[^"]*ui-page[^"]*"/, `${page} should use ui-page`);
  }

  const system = await readSource('src/styles/page-system.css');
  for (const rootClass of ['mail-view', 'compose-page', 'account-page', 'settings-page', 'backup-page', 'contact-page']) {
    assert.match(system, new RegExp(`\\.${rootClass}`));
  }
});

test('shared component styles keep literal palette values inside tokens only', async () => {
  for (const file of ['src/styles/components.css', 'src/styles/app-shell.css', 'src/styles/page-system.css']) {
    const source = await readSource(file);
    assert.doesNotMatch(source, /#[0-9a-f]{3,8}/gi, `${file} should use semantic tokens`);
    assert.doesNotMatch(source, /rgba?\(/gi, `${file} should use semantic tokens`);
  }
});

test('page and component style blocks use semantic colors instead of fixed light-theme literals', async () => {
  const files = [
    ...await listVueFiles('src/views'),
    ...await listVueFiles('src/components'),
  ];

  for (const file of files) {
    const source = await readSource(file);
    const styleBlocks = [...source.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)];
    for (const [, rawStyle] of styleBlocks) {
      const style = rawStyle.replace(/\/\*[\s\S]*?\*\//g, '');
      assert.doesNotMatch(style, /#[0-9a-f]{3,8}\b|rgba?\(/gi, `${file} style should use semantic tokens`);
    }
  }
});

test('high-density layout measurements are centralized as semantic tokens', async () => {
  const tokens = await readSource('src/styles/tokens.css');
  const layout = await readSource('src/styles/layout-system.css');

  for (const declaration of [
    '--page-gutter: 24px',
    '--page-gutter-compact: 16px',
    '--page-section-gap: 16px',
    '--panel-padding: 16px',
    '--control-height-md: 36px',
    '--toolbar-height: 48px',
    '--list-row-height: 48px',
  ]) {
    assert.match(tokens, new RegExp(declaration.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  }
  assert.match(layout, /padding:\s*var\(--page-gutter\)/);
  assert.match(layout, /gap:\s*var\(--page-section-gap\)/);
  assert.match(layout, /min-height:\s*var\(--toolbar-height\)/);
  assert.match(layout, /scrollbar-gutter:\s*stable/);
});

test('layout primitives expose the four approved page templates', async () => {
  const frame = await readSource('src/components/layout/PageFrame.vue');
  const layout = await readSource('src/styles/layout-system.css');
  const empty = await readSource('src/components/ui/UiEmptyState.vue');

  assert.match(frame, /'workspace'\s*\|\s*'management'\s*\|\s*'split'\s*\|\s*'document'/);
  assert.match(layout, /\.page-frame--workspace/);
  assert.match(layout, /\.page-frame--management/);
  assert.match(layout, /\.page-frame--split/);
  assert.match(layout, /\.page-frame--document/);
  assert.match(empty, /ui-empty-state/);
});

test('page width tokens distinguish fluid workspaces from bounded forms', async () => {
  const tokens = await readSource('src/styles/tokens.css');

  assert.match(tokens, /--page-gutter:\s*24px/);
  assert.match(tokens, /--page-gutter-compact:\s*16px/);
  assert.match(tokens, /--page-form-max:\s*1120px/);
  assert.match(tokens, /--page-reading-max:\s*960px/);
  assert.doesNotMatch(tokens, /--page-content-max:\s*1280px/);
});

test('product primitives cover fields badges segmented controls and card density', async () => {
  const field = await readSource('src/components/ui/UiField.vue');
  const badge = await readSource('src/components/ui/UiBadge.vue');
  const segmented = await readSource('src/components/ui/UiSegmentedControl.vue');
  const card = await readSource('src/components/ui/UiCard.vue');

  assert.match(field, /error\?: string/);
  assert.match(field, /ui-field__message/);
  assert.match(badge, /'neutral' \| 'accent' \| 'success' \| 'warning' \| 'danger'/);
  assert.match(segmented, /update:modelValue/);
  assert.match(segmented, /aria-label/);
  assert.match(card, /'default' \| 'subtle' \| 'raised'/);
  assert.match(card, /'none' \| 'sm' \| 'md' \| 'lg'/);
});

test('formal main stylesheet order establishes V2 tokens before base and layout', async () => {
  const source = await readSource('src/main.ts');
  const tokens = source.indexOf("./styles/v2-tokens.css");
  const base = source.indexOf("./styles/v2-base.css");
  const layout = source.indexOf("./styles/v2-layout.css");

  assert.ok(tokens >= 0 && base > tokens && layout > base);
  assert.doesNotMatch(source, /\.\/styles\/(?:macos|components|layout-system|page-system)\.css/);
});

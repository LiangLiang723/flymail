import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, writeFile, mkdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
  FocusReturnStack,
  isTextEditingTarget,
  nextRovingIndex,
  shouldHandleShortcut,
} from '../../src/shared/accessibility/focus.ts';
import { measureBundleBudget } from '../../scripts/check-v2-bundle-budget.mjs';

test('shortcut guard ignores editors and roving index remains bounded', () => {
  assert.equal(isTextEditingTarget({ tagName: 'INPUT' }), true);
  assert.equal(isTextEditingTarget({ tagName: 'DIV', isContentEditable: true }), true);
  assert.equal(isTextEditingTarget({ tagName: 'DIV', getAttribute: (name: string) => name === 'role' ? 'textbox' : null }), true);
  assert.equal(isTextEditingTarget({ tagName: 'BUTTON' }), false);
  assert.equal(shouldHandleShortcut({ target: { tagName: 'TEXTAREA' }, defaultPrevented: false, isComposing: false }), false);
  assert.equal(shouldHandleShortcut({ target: { tagName: 'DIV' }, defaultPrevented: false, isComposing: false }), true);
  assert.equal(nextRovingIndex(0, -1, 3), 2);
  assert.equal(nextRovingIndex(2, 1, 3), 0);
  assert.equal(nextRovingIndex(0, 1, 0), -1);
});

test('focus return stack restores the latest still-connected element', () => {
  const calls: string[] = [];
  const stack = new FocusReturnStack();
  stack.push({ isConnected: true, focus: () => calls.push('first') });
  stack.push({ isConnected: false, focus: () => calls.push('stale') });
  stack.restore();
  assert.deepEqual(calls, ['first']);
});

test('shared styles expose text-plus-icon errors touch targets themes and reduced motion', async () => {
  const tokens = await readFile(new URL('../../src/styles/v2-tokens.css', import.meta.url), 'utf8');
  const base = await readFile(new URL('../../src/styles/v2-base.css', import.meta.url), 'utf8');
  const app = await readFile(new URL('../../src/app/AppV2.vue', import.meta.url), 'utf8');
  const drawer = await readFile(new URL('../../src/features/navigation/MobileNavigationDrawer.vue', import.meta.url), 'utf8');
  assert.match(tokens, /data-theme='light'/);
  assert.match(tokens, /data-theme='dark'/);
  assert.match(tokens, /prefers-color-scheme:\s*dark/);
  assert.match(base, /min-width:\s*44px/);
  assert.match(base, /min-height:\s*44px/);
  assert.match(base, /\.v2-error::before/);
  assert.match(base, /prefers-reduced-motion:\s*reduce/);
  assert.match(base, /transition-duration:\s*1ms\s*!important/);
  assert.match(app, /applyAppearance/);
  assert.match(app, /shouldHandleShortcut/);
  assert.match(drawer, /createFocusTrap/);
});

test('V2 source keeps heavy pages lazy and splits editor runtime', async () => {
  const router = await readFile(new URL('../../src/app/router.ts', import.meta.url), 'utf8');
  const app = await readFile(new URL('../../src/app/AppV2.vue', import.meta.url), 'utf8');
  const config = await readFile(new URL('../../vite.config.ts', import.meta.url), 'utf8');
  assert.match(router, /component:\s*\(\)\s*=>\s*import\('\.\.\/features\/admin\/AdminPage\.vue'\)/);
  assert.match(router, /component:\s*\(\)\s*=>\s*import\('\.\.\/features\/backup\/BackupPage\.vue'\)/);
  assert.match(router, /component:\s*\(\)\s*=>\s*import\('\.\.\/features\/sync-center\/SyncCenterPage\.vue'\)/);
  assert.doesNotMatch(app, /features\/(?:compose|admin|backup|sync-center)\//);
  assert.match(config, /editor-core/);
  assert.match(config, /editor-runtime/);
  assert.match(config, /editor-extensions/);
  assert.doesNotMatch(config, /return\s+['"]editor['"]/);
});

test('bundle checker measures initial closure and every async chunk', async () => {
  const root = await mkdtemp(join(tmpdir(), 'flymail-v2-budget-'));
  try {
    const assets = join(root, 'assets');
    const manifestDir = join(root, '.vite');
    await mkdir(assets, { recursive: true });
    await mkdir(manifestDir, { recursive: true });
    await writeFile(join(assets, 'entry.js'), 'const entry = 1;'.repeat(100));
    await writeFile(join(assets, 'core.js'), 'const core = 1;'.repeat(100));
    await writeFile(join(assets, 'async.js'), 'const asyncPage = 1;'.repeat(100));
    await writeFile(join(manifestDir, 'manifest.json'), JSON.stringify({
      'v2.html': { file: 'assets/entry.js', isEntry: true, imports: ['_core.js'] },
      '_core.js': { file: 'assets/core.js' },
      'src/features/compose/ComposePage.vue': { file: 'assets/async.js', isDynamicEntry: true },
    }));
    const result = await measureBundleBudget(root, { initialLimitBytes: 180 * 1024, asyncLimitBytes: 120 * 1024 });
    assert.equal(result.withinBudget, true);
    assert.equal(result.initialFiles.length, 2);
    assert.equal(result.asyncFiles.length, 1);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

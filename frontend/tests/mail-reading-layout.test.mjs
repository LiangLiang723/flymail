import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

async function readSource(relativePath) {
  return readFile(path.join(frontendRoot, relativePath), 'utf8');
}

function cssBlock(source, selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = source.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, 's'));
  assert.ok(match, `missing CSS block for ${selector}`);
  return match[1];
}

test('long mail bodies delegate both axes to the viewport scroll container', async () => {
  const source = await readSource('src/views/MailList.vue');
  const detailBody = cssBlock(source, '.detail-body');
  const contentWrap = cssBlock(source, '.detail-content-wrap');

  assert.match(detailBody, /overflow-y:\s*auto/);
  assert.match(detailBody, /overflow-x:\s*auto/);
  assert.match(contentWrap, /overflow:\s*visible/);
  assert.doesNotMatch(contentWrap, /overflow-x:\s*auto/);
  assert.doesNotMatch(source, /\.detail-content\s+:deep\(\*\)\s*\{[^}]*max-width:\s*100%/s);
});

test('mail theme adaptation clamps negative inline margins that shift content left', async () => {
  const layoutSource = await readSource('src/utils/mail-body-layout.ts');
  const themeSource = await readSource('src/utils/mail-body-theme.ts');

  assert.match(layoutSource, /export function isNegativeCssLength/);
  assert.match(themeSource, /margin-left/);
  assert.match(themeSource, /margin-inline-start/);
  assert.match(themeSource, /clampNegativeHorizontalMargins\(element\)/);
});

test('signature panel toolbar paints above the recipient form stacking context', async () => {
  const source = await readSource('src/views/ComposeEmail.vue');
  const toolbar = cssBlock(source, '.compose-toolbar');

  assert.match(toolbar, /position:\s*relative/);
  assert.match(toolbar, /z-index:\s*[1-9]\d*/);
  assert.match(toolbar, /overflow:\s*visible/);
});

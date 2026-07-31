import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (file) => readFile(path.join(root, file), 'utf8');

test('shared page patterns own reusable controls and surfaces', async () => {
  const components = await read('src/styles/components.css');
  const pages = await read('src/styles/page-system.css');

  for (const selector of ['.ui-input', '.ui-select', '.ui-textarea', '.ui-checkbox', '.ui-badge', '.ui-segmented']) {
    assert.match(components, new RegExp(selector.replace('.', '\\.')));
  }
  for (const selector of ['.ui-section', '.ui-stat-grid', '.ui-list-row', '.ui-detail-grid']) {
    assert.match(pages, new RegExp(selector.replace('.', '\\.')));
  }
});

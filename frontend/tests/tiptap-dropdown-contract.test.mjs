import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const readSource = (relativePath) => readFile(path.join(frontendRoot, relativePath), 'utf8');

test('tiptap uses one explicit dropdown state', async () => {
  const source = await readSource('src/components/TiptapEditor.vue');

  assert.match(source, /const activeDropdown = ref<ToolbarDropdown>\(null\)/);
  assert.match(source, /function toggleDropdown/);
  assert.match(source, /function closeDropdown/);
  assert.match(source, /function runDropdownAction/);
  assert.match(source, /aria-expanded/);
  assert.match(source, /@keydown\.escape/);
  assert.match(source, /activeDropdown === 'emoji'/);
  assert.doesNotMatch(source, /\.toolbar-dropdown:hover \.dropdown-menu/);
  assert.doesNotMatch(source, /\.toolbar-dropdown:focus-within \.dropdown-menu/);
});

test('tiptap dropdowns close on outside pointer and captured scroll', async () => {
  const source = await readSource('src/components/TiptapEditor.vue');

  assert.match(source, /window\.addEventListener\('pointerdown'/);
  assert.match(source, /window\.addEventListener\('scroll', closeDropdown, true\)/);
  assert.match(source, /window\.removeEventListener\('pointerdown'/);
  assert.match(source, /window\.removeEventListener\('scroll', closeDropdown, true\)/);
  assert.match(source, /max-width:\s*min\(320px, calc\(100vw - 24px\)\)/);
  assert.match(source, /max-height:\s*min\(360px, calc\(100vh - 96px\)\)/);
});

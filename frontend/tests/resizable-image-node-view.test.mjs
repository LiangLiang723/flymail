import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), 'utf8');

test('resizable image node view exposes drag and keyboard-accessible quick sizes', async () => {
  const source = await read('src/utils/resizable-image-node-view.ts');

  assert.match(source, /aria-label', '调整图片大小'/);
  assert.match(source, /\[25, 50, 75, 100\]/);
  assert.match(source, /pointermove/);
  assert.match(source, /pointerup/);
  assert.match(source, /setNodeMarkup/);
  assert.match(source, /imageWidthFromPercent/);
  assert.match(source, /resizable-image-node--selected/);
});

test('initial image sync does not read editor.view before Tiptap mounts it', async () => {
  const source = await read('src/utils/resizable-image-node-view.ts');
  const syncBlock = source.match(/function syncImageAttributes\(\) \{([\s\S]*?)\n  \}\n\n  function selectCurrentNode/);

  assert.ok(syncBlock, 'syncImageAttributes should remain a focused helper');
  assert.doesNotMatch(syncBlock[1], /editorWidth\(\)/);
  assert.doesNotMatch(syncBlock[1], /editor\.view/);
});

test('tiptap connects the resizable node view and styles its controls', async () => {
  const source = await read('src/components/TiptapEditor.vue');

  assert.match(source, /createResizableImageNodeView/);
  assert.match(source, /addNodeView\(\)/);
  assert.match(source, /return createResizableImageNodeView/);
  assert.match(source, /\.image-resize-handle/);
  assert.match(source, /\.image-size-toolbar/);
  assert.match(source, /:focus-visible/);
});

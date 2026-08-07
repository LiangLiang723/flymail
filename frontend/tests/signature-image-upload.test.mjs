import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const readSource = (relativePath) => readFile(path.join(frontendRoot, relativePath), 'utf8');

test('tiptap image button uploads a local image instead of asking for a URL', async () => {
  const source = await readSource('src/components/TiptapEditor.vue');

  assert.match(source, /ref="imageInput"/);
  assert.match(source, /accept="image\/jpeg,image\/png,image\/webp"/);
  assert.match(source, /function openImagePicker/);
  assert.match(source, /async function handleImageUpload/);
  assert.match(source, /async function uploadEditorImage/);
  assert.match(source, /api\.post\('\/signatures\/images', formData/);
  assert.match(source, /data\?*\.image_id/);
  assert.match(source, /managedSignatureImageSource/);
  assert.match(source, /signatureImageId/);
  assert.match(source, /data-flymail-signature-image/);
  assert.match(source, /handlePaste/);
  assert.match(source, /clipboardData/);
  assert.match(source, /getAsFile\(\)/);
  assert.match(source, /uploadEditorImage\(file/);
  assert.doesNotMatch(source, /setImage\(\{ src: data\.url \}\)/);
  assert.match(source, /uploadingImage/);
  assert.doesNotMatch(source, /输入图片地址/);
});

test('tiptap image node persists a sanitized width attribute', async () => {
  const source = await readSource('src/components/TiptapEditor.vue');

  assert.match(source, /const ResizableImage = Image\.extend/);
  assert.match(source, /parseImageWidth/);
  assert.match(source, /width:\s*\{/);
  assert.match(source, /renderHTML:[\s\S]*width:\s*String\(Math\.round\(attributes\.width\)\)/);
  assert.match(source, /ResizableImage,/);
});

test('compose signature menu uses a fully opaque surface', async () => {
  const source = await readSource('src/styles/page-system.css');

  assert.match(
    source,
    /\.compose-page \.sig-panel\s*\{[\s\S]*?background:\s*var\(--ui-surface-1\)\s*!important;[\s\S]*?backdrop-filter:\s*none;/,
  );
  const floatingGroup = source.match(
    /\.compose-page \.contact-suggestions,\s*\.tiptap-editor \.dropdown-menu,\s*\.tiptap-editor \.emoji-picker\s*\{\s*background:\s*var\(--ui-surface-floating\)/,
  );
  assert.ok(floatingGroup, 'other floating surfaces should keep the shared floating treatment');
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const readSource = (relativePath) => readFile(path.join(frontendRoot, relativePath), 'utf8');

test('signature management is a full split workspace', async () => {
  const source = await readSource('src/views/SignatureManagement.vue');

  assert.match(source, /<PageFrame[^>]*template="split"[^>]*width="fluid"/);
  assert.match(source, /签名管理/);
  assert.match(source, /新建签名/);
  assert.match(source, /搜索签名/);
  assert.match(source, /全部邮箱/);
  assert.match(source, /新邮件默认/);
  assert.match(source, /回复\/转发默认/);
  assert.match(source, /<TiptapEditor/);
  assert.match(source, /beginDuplicate/);
});

test('signature management protects unsaved work and supports mobile editing', async () => {
  const source = await readSource('src/views/SignatureManagement.vue');

  assert.match(source, /beforeunload/);
  assert.match(source, /showConfirm/);
  assert.match(source, /confirmDiscardChanges/);
  assert.match(source, /mobileEditing/);
  assert.match(source, /返回列表/);
  assert.doesNotMatch(source, /modal-overlay/);
  assert.doesNotMatch(source, /bottom-sheet/);
  assert.match(source, /\.signature-editor-pane\s*\{[^}]*overflow:\s*hidden;/s);
  assert.match(source, /\.signature-editor-content\s*\{[^}]*overflow-y:\s*auto;/s);
  assert.doesNotMatch(source, /\.signature-action-bar\s*\{[^}]*position:\s*sticky;/s);
});

test('signature creation offers five persisted starting templates', async () => {
  const source = await readSource('src/views/SignatureManagement.vue');

  for (const key of ['blank', 'business', 'contact', 'brand', 'minimal']) {
    assert.match(source, new RegExp(`key: '${key}'`));
  }
  assert.match(source, /signatureStore\.beginCreate/);
  assert.match(source, /signatureStore\.draft\.content_html/);
});

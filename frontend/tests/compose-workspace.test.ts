import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const readSource = (relativePath: string) => readFile(path.join(frontendRoot, relativePath), 'utf8');

test('mail store exposes an in-memory complete compose workspace snapshot', async () => {
  const source = await readSource('src/stores/mail.ts');
  for (const field of [
    'account_id', 'to', 'cc', 'bcc', 'subject', 'body_html', 'attachments',
    'draft_message_id', 'draft_folder', 'compose_kind', 'show_cc', 'show_bcc',
    'active_signature_id',
  ]) {
    assert.match(source, new RegExp(`\\b${field}\\b`));
  }
  assert.match(source, /const composeWorkspace = ref<ComposeWorkspaceSnapshot \| null>\(null\)/);
  assert.match(source, /function saveComposeWorkspace/);
  assert.match(source, /function clearComposeWorkspace/);
  assert.doesNotMatch(source, /sessionStorage\.setItem\([^\n]*composeWorkspace/);
  assert.doesNotMatch(source, /localStorage\.setItem\([^\n]*composeWorkspace/);
});

test('compose restores the workspace without applying a second default signature', async () => {
  const source = await readSource('src/views/ComposeEmail.vue');
  assert.match(source, /function buildComposeWorkspaceSnapshot/);
  assert.match(source, /mailStore\.saveComposeWorkspace\(buildComposeWorkspaceSnapshot\(\)\)/);
  assert.match(source, /signatureStore\.setEntrySource\('compose'\)/);
  assert.match(source, /applyDefaultSignature:\s*false/);
  assert.match(source, /attachments\.value = \(draft\?\.attachments \|\| \[\]\)\.map/);
  assert.match(source, /showCc\.value = Boolean\(draft\?\.show_cc/);
  assert.match(source, /showBcc\.value = Boolean\(draft\?\.show_bcc/);
  assert.match(source, /activeSignatureId\.value = draft\?\.active_signature_id/);
});

test('compose clears the saved workspace only for send, discard, or a new compose draft', async () => {
  const source = await readSource('src/views/ComposeEmail.vue');
  assert.ok((source.match(/mailStore\.clearComposeWorkspace\(\)/g) || []).length >= 3);
  assert.match(source, /showConfirm\([^)]*\(\) => \{\s*mailStore\.clearComposeWorkspace\(\);\s*emit\('discard'\)/s);
  assert.match(source, /watch\([\s\S]*mailStore\.composeDraft[\s\S]*clearComposeWorkspace\(\)/);
});

import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const readSource = (relativePath) => readFile(path.join(frontendRoot, relativePath), 'utf8');

test('compose fields share one label and control grid', async () => {
  const source = await readSource('src/views/ComposeEmail.vue');
  assert.ok((source.match(/class="compose-field-label"/g) || []).length >= 5);
  assert.ok((source.match(/class="compose-field-control"/g) || []).length >= 5);
  assert.match(source, /grid-template-columns:\s*72px minmax\(0,\s*1fr\)/);
});

test('signature manager persists account scope and separate defaults', async () => {
  const source = await readSource('src/views/ComposeEmail.vue');
  assert.match(source, /无签名/);
  assert.match(source, /管理签名/);
  assert.match(source, /const editingUserSigAccountId = ref/);
  assert.match(source, /const editingUserSigIsDefault = ref/);
  assert.match(source, /const editingUserSigIsReplyDefault = ref/);
  assert.match(source, /editingUserSigAccountId\.value = sig \? sig\.account_id/);
  assert.match(source, /function openSignatureManager/);
  assert.match(source, /account_id:\s*editingUserSigAccountId\.value/);
  assert.match(source, /is_default:\s*editingUserSigIsDefault\.value/);
  assert.match(source, /is_reply_default:\s*editingUserSigIsReplyDefault\.value/);
});

test('compose context selects the correct account default signature', async () => {
  const composeSource = await readSource('src/views/ComposeEmail.vue');
  const storeSource = await readSource('src/stores/mail.ts');
  const replySource = await readSource('src/composables/useReplyForward.ts');
  const mailListSource = await readSource('src/views/MailList.vue');

  assert.match(composeSource, /function resolveDefaultSignature/);
  assert.match(composeSource, /composeKind\.value = draft\?\.compose_kind/);
  assert.match(storeSource, /compose_kind\?: 'new' \| 'reply' \| 'forward' \| 'draft'/);
  assert.match(replySource, /compose_kind:\s*'reply'/);
  assert.match(replySource, /compose_kind:\s*'forward'/);
  assert.match(mailListSource, /compose_kind:\s*'new'/);
  assert.match(mailListSource, /compose_kind:\s*'draft'/);
});

test('tiptap exposes a replaceable managed signature block', async () => {
  const source = await readSource('src/components/TiptapEditor.vue');
  assert.match(source, /signatureBlock/);
  assert.match(source, /data-flymail-signature/);
  assert.match(source, /function setManagedSignature/);
  assert.match(source, /defineExpose\([^)]*setManagedSignature/s);
});

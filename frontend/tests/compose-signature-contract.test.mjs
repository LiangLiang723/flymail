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

test('compose keeps signature selection quick and delegates management', async () => {
  const source = await readSource('src/views/ComposeEmail.vue');
  assert.match(source, /当前签名/);
  assert.match(source, /无签名/);
  assert.match(source, /管理签名/);
  assert.match(source, /function openSignatureManager/);
  assert.match(source, /signatureStore\.signatures/);
  assert.doesNotMatch(source, /内置模板/);
  assert.doesNotMatch(source, /showCustomizeDialog/);
  assert.doesNotMatch(source, /showEditUserSigDialog/);
  assert.doesNotMatch(source, /editingUserSigHtml/);
  assert.doesNotMatch(source, /<TiptapEditor[^>]*class="signature-editor"/);
  assert.doesNotMatch(source, /api\.(post|put|delete)\(`?\/signatures/);
});

test('compose context selects the correct account default signature', async () => {
  const composeSource = await readSource('src/views/ComposeEmail.vue');
  const storeSource = await readSource('src/stores/mail.ts');
  const replySource = await readSource('src/composables/useReplyForward.ts');
  const mailListSource = await readSource('src/views/MailList.vue');

  assert.match(composeSource, /import \{ resolveDefaultSignature \} from '\.\.\/utils\/signature-management'/);
  assert.match(composeSource, /resolveDefaultSignature\(userSigs\.value, fromAccountId\.value, composeKind\.value\)/);
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
  assert.match(source, /atom:\s*true/);
  assert.match(source, /data-flymail-signature-spacer/);
  assert.match(source, /TextSelection/);
  assert.doesNotMatch(source, /chain\(\)\.focus\(\)\.insertContentAt\(position, signatureHtml\)/);
});

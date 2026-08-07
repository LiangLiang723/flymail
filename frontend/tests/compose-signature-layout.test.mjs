import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const readSource = (relativePath) => readFile(path.join(frontendRoot, relativePath), 'utf8');

test('managed signature is atomic and selection returns to the first body paragraph', async () => {
  const source = await readSource('src/components/TiptapEditor.vue');

  assert.match(source, /name:\s*'signatureBlock'[\s\S]*?atom:\s*true/);
  assert.match(source, /data-flymail-signature-spacer/);
  assert.match(source, /TextSelection\.create\(transaction\.doc,\s*1\)/);
  assert.match(source, /transaction\s*=\s*transaction\.setSelection/);
  assert.match(source, /signatureSpacer/);
});

test('signature replacement preserves all non-signature nodes in one transaction', async () => {
  const source = await readSource('src/components/TiptapEditor.vue');

  assert.match(source, /let transaction = currentEditor\.state\.tr/);
  assert.match(source, /node\.type\.name === 'signatureBlock'/);
  assert.match(source, /transaction = transaction\.delete/);
  assert.match(source, /transaction = transaction\.insert/);
  assert.match(source, /currentEditor\.view\.dispatch\(transaction\.scrollIntoView\(\)\)/);
  assert.doesNotMatch(source, /chain\(\)\.focus\(\)\.insertContentAt\(position, signatureHtml\)/);
});

test('new reply and forward drafts start with a real empty input paragraph', async () => {
  const composeSource = await readSource('src/views/ComposeEmail.vue');
  const editorSource = await readSource('src/components/TiptapEditor.vue');
  const replySource = await readSource('src/composables/useReplyForward.ts');

  assert.match(editorSource, /const MailQuote = Node\.create\(\{[\s\S]*?priority:\s*1000/);

  assert.match(composeSource, /bodyHtml\.value = draft\?\.body_html \|\| '<p><\/p>'/);
  assert.match(replySource, /const quoteHtml = `<p><\/p><blockquote data-flymail-quote="reply"/);
  assert.match(replySource, /const fwdHtml = `<p><\/p><div data-flymail-quote="forward"/);
  assert.doesNotMatch(replySource, /<p><br><\/p><(?:blockquote|div) data-flymail-quote/);
  assert.match(replySource, /compose_kind:\s*'reply'/);
  assert.match(replySource, /compose_kind:\s*'forward'/);
});

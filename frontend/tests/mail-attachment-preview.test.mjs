import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const readSource = (relativePath) => readFile(path.join(frontendRoot, relativePath), 'utf8');

test('common safe attachment types are classified for native preview', async () => {
  const module = await import('../src/utils/attachment-preview.ts').catch(() => null);
  assert.ok(module, 'attachment preview utility must exist');

  const classify = module.getAttachmentPreviewKind;
  assert.equal(classify({ content_type: 'image/png', filename: 'shot.png' }), 'image');
  assert.equal(classify({ content_type: 'application/octet-stream', filename: 'photo.jpg' }), 'image');
  assert.equal(classify({ content_type: 'application/pdf', filename: 'report.pdf' }), 'pdf');
  assert.equal(classify({ content_type: 'text/plain; charset=utf-8', filename: 'notes.txt' }), 'text');
  assert.equal(classify({ content_type: 'audio/mpeg', filename: 'voice.mp3' }), 'audio');
  assert.equal(classify({ content_type: 'video/mp4', filename: 'clip.mp4' }), 'video');
  assert.equal(classify({ content_type: 'image/svg+xml', filename: 'unsafe.svg' }), null);
  assert.equal(classify({ content_type: 'text/html', filename: 'unsafe.html' }), null);
  assert.equal(classify({ content_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', filename: 'doc.docx' }), null);
});

test('mail detail places attachment actions above the body and wires the preview dialog', async () => {
  const source = await readSource('src/views/MailList.vue');
  const preview = await readSource('src/components/mail/AttachmentPreview.vue').catch(() => '');

  const headerIndex = source.indexOf('class="detail-header"');
  const attachmentIndex = source.indexOf('class="attachment-list"');
  const bodyIndex = source.indexOf('class="detail-content-wrap"');
  assert.ok(headerIndex >= 0 && attachmentIndex > headerIndex && bodyIndex > attachmentIndex);

  assert.match(source, /getAttachmentPreviewKind\(att\)/);
  assert.match(source, /@click="previewAttachment\(att\)"/);
  assert.match(source, /<AttachmentPreview/);
  assert.match(source, /responseType:\s*'blob'/);
  assert.match(source, /URL\.createObjectURL/);
  assert.match(source, /URL\.revokeObjectURL/);

  assert.match(preview, /role="dialog"/);
  assert.match(preview, /<img\b/);
  assert.match(preview, /<iframe\b/);
  assert.match(preview, /<audio\b/);
  assert.match(preview, /<video\b/);
  assert.match(preview, /event\.key === 'Escape'/);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), 'utf8');

test('themed mail rendering stays separate from neutral rendering', async () => {
  const sanitize = await read('src/utils/sanitize.ts');
  const mail = await read('src/views/MailList.vue');
  const backup = await read('src/views/Backup.vue');
  const reply = await read('src/composables/useReplyForward.ts');
  const pdf = await read('src/utils/export-pdf.ts');

  assert.match(sanitize, /export function renderThemedMailBody/);
  assert.match(mail, /renderThemedMailBody/);
  assert.match(backup, /renderThemedMailBody/);
  assert.match(reply, /renderMailBody/);
  assert.doesNotMatch(reply, /renderThemedMailBody/);
  assert.match(pdf, /renderMailBody/);
  assert.doesNotMatch(pdf, /renderThemedMailBody/);
});

test('mail theme variables are scoped to reading content', async () => {
  const tokens = await read('src/styles/tokens.css');
  const styles = await read('src/styles/page-system.css');
  assert.match(tokens, /--ui-mail-body-bg-light:/);
  assert.match(tokens, /--ui-mail-body-bg-dark:/);
  assert.match(styles, /\.detail-content \.flymail-mail-color/);
  assert.match(styles, /:root\.dark \.detail-content \.flymail-mail-color/);
  assert.match(styles, /--flymail-mail-color-light/);
  assert.match(styles, /--flymail-mail-color-dark/);
  assert.doesNotMatch(styles, /\.detail-content\s+img[^}]*filter:\s*invert/is);
});

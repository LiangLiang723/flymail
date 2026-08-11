import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (file) => readFile(path.join(frontendRoot, file), 'utf8');

test('mail message type exposes the optional verification code returned by the API', async () => {
  const source = await read('src/types/mail.ts');

  assert.match(source, /verification_code\?: string/);
});

test('mail rows render a real copy button without nesting buttons', async () => {
  const source = await read('src/views/MailList.vue');

  assert.match(source, /<div[\s\S]*class="mail-item"[\s\S]*role="button"[\s\S]*tabindex="0"/s);
  assert.doesNotMatch(source, /<button[\s\S]{0,240}class="mail-item"/s);
  assert.match(source, /v-if="msg\.verification_code && !selectMode"/);
  assert.match(source, /class="verification-code-copy"/);
  assert.match(source, /class="verification-code-copy"[\s\S]*title="复制验证码"[\s\S]*@click\.stop="copyVerificationCode\(msg\.verification_code\)"/s);
  assert.match(source, /class="verification-code-copy"[\s\S]*<svg[^>]*aria-hidden="true"/s);
  assert.doesNotMatch(source, /class="verification-code-copy"[\s\S]{0,500}>\s*复制验证码\s*<\/button>/s);
  assert.doesNotMatch(source, /class="mail-status-tag"/);
  const copyIndex = source.indexOf('class="verification-code-copy"');
  const dateIndex = source.indexOf('<span class="mail-date">');
  assert.ok(copyIndex >= 0 && dateIndex > copyIndex);
});

test('mail row optional metadata uses one dynamic action rail before the date', async () => {
  const source = await read('src/views/MailList.vue');
  const mainRowStart = source.indexOf('<div class="mail-main-row">');
  const mainRowEnd = source.indexOf('</div>', mainRowStart);
  const mainRow = source.slice(mainRowStart, mainRowEnd);

  assert.ok(mainRowStart >= 0 && mainRowEnd > mainRowStart);
  assert.doesNotMatch(mainRow, /class="att-badge"/);
  assert.match(
    source,
    /v-if="msg\.has_attachments \|\| \(msg\.verification_code && !selectMode\)"\s+class="mail-meta-actions"/,
  );
  assert.match(source, /<svg v-if="msg\.has_attachments" class="att-badge" width="15" height="15"/);

  const metaStart = source.indexOf('class="mail-meta-actions"');
  const copyIndex = source.indexOf('class="verification-code-copy"', metaStart);
  const attachmentIndex = source.indexOf('class="att-badge"', metaStart);
  const metaEnd = source.indexOf('</div>', metaStart);
  const dateIndex = source.indexOf('<span class="mail-date">', metaEnd);

  assert.ok(metaStart >= 0);
  assert.ok(copyIndex > metaStart);
  assert.ok(attachmentIndex > copyIndex);
  assert.ok(metaEnd > attachmentIndex);
  assert.ok(dateIndex > metaEnd);
});

test('verification code copy action is keyboard safe and does not open the mail row', async () => {
  const source = await read('src/views/MailList.vue');

  assert.match(source, /@keydown\.enter\.self="openMessageRow\(msg\)"/);
  assert.match(source, /@keydown\.space\.prevent\.self="openMessageRow\(msg\)"/);
  assert.match(source, /async function copyVerificationCode\(code: string\)/);
  assert.match(source, /navigator\.clipboard\.writeText\(code\)/);
  assert.match(source, /document\.execCommand\('copy'\)/);
  assert.match(source, /uiStore\.success\('验证码已复制'\)/);
});

test('copy button stays fixed while subject owns shrinking on desktop and mobile', async () => {
  const source = await read('src/views/MailList.vue');

  assert.match(source, /\.verification-code-copy\s*\{[^}]*flex-shrink:\s*0;/s);
  assert.match(source, /\.verification-code-copy\s*\{[^}]*width:\s*28px;[^}]*min-width:\s*28px;[^}]*height:\s*28px;/s);
  assert.match(source, /\.verification-code-copy\s*\{[^}]*color:\s*var\(--text-tertiary\);/s);
  assert.doesNotMatch(source, /\.verification-code-copy\s*\{[^}]*min-width:\s*78px;/s);
  assert.match(source, /\.mail-subject\s*\{[^}]*min-width:\s*0;/s);
  assert.match(source, /@media \(max-width:\s*768px\)[\s\S]*\.verification-code-copy\s*\{[^}]*width:\s*28px;[^}]*min-width:\s*28px;[^}]*height:\s*28px;/s);
  assert.doesNotMatch(source, /@media \(max-width:\s*768px\)[\s\S]*\.verification-code-copy\s*\{[^}]*grid-area:\s*code;/s);
  assert.doesNotMatch(source, /@media \(max-width:\s*768px\)[\s\S]*\.verification-code-copy\s*\{[^}]*min-width:\s*72px;/s);
  assert.match(source, /@media \(max-width:\s*768px\)[\s\S]*grid-template-areas:[\s\S]*"select sender date"[\s\S]*"select info meta"/s);
  assert.match(source, /@media \(max-width:\s*768px\)[\s\S]*\.mail-meta-actions\s*\{[^}]*grid-area:\s*meta;[^}]*justify-self:\s*end;[^}]*margin:\s*0;[^}]*gap:\s*6px;/s);
  assert.match(source, /@media \(max-width:\s*768px\)[\s\S]*\.list-items\s*\{[^}]*overflow-x:\s*hidden;/s);
});

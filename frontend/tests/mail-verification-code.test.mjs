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
  assert.match(source, /class="verification-code-copy"[\s\S]*title="复制验证码"[\s\S]*@click\.stop="copyVerificationCode\(msg\)"/s);
  assert.match(source, /class="verification-code-copy"[\s\S]*<svg[^>]*aria-hidden="true"/s);
  assert.doesNotMatch(source, /class="verification-code-copy"[\s\S]{0,500}>\s*复制验证码\s*<\/button>/s);
  assert.doesNotMatch(source, /class="mail-status-tag"/);
  const copyIndex = source.indexOf('class="verification-code-copy"');
  const dateIndex = source.indexOf('<span class="mail-date">');
  assert.ok(copyIndex >= 0 && dateIndex > copyIndex);
});

test('desktop mail metadata follows the title while date remains the final row item', async () => {
  const source = await read('src/views/MailList.vue');
  const infoStart = source.indexOf('class="mail-info"');
  const titleContentStart = source.indexOf('class="mail-title-content"', infoStart);
  const subjectIndex = source.indexOf('class="mail-subject"', titleContentStart);
  const conversationIndex = source.indexOf('class="conversation-count"', subjectIndex);
  const titleContentEnd = source.indexOf('</div>', conversationIndex);
  const mainRowEnd = source.indexOf('</div>', titleContentEnd + 6);
  const infoEnd = source.indexOf('</div>', mainRowEnd + 6);
  const metaStart = source.indexOf('class="mail-meta-actions"', infoEnd);
  const copyIndex = source.indexOf('class="verification-code-copy"', metaStart);
  const attachmentIndex = source.indexOf('class="att-badge"', copyIndex);
  const metaEnd = source.indexOf('</div>', attachmentIndex);
  const dateIndex = source.indexOf('<span class="mail-date">', metaEnd);

  assert.ok(infoStart >= 0);
  assert.ok(titleContentStart > infoStart);
  assert.ok(subjectIndex > titleContentStart);
  assert.ok(conversationIndex > subjectIndex);
  assert.ok(titleContentEnd > conversationIndex);
  assert.ok(mainRowEnd > titleContentEnd);
  assert.ok(infoEnd > mainRowEnd);
  assert.ok(metaStart > infoEnd);
  assert.ok(copyIndex > metaStart);
  assert.ok(attachmentIndex > copyIndex);
  assert.ok(metaEnd > attachmentIndex);
  assert.ok(dateIndex > metaEnd);
  assert.match(
    source,
    /v-if="msg\.has_attachments \|\| \(msg\.verification_code && !selectMode\)"\s+class="mail-meta-actions"/,
  );
});

test('verification code copy action is keyboard safe and marks the copied unread message as read', async () => {
  const source = await read('src/views/MailList.vue');

  assert.match(source, /@keydown\.enter\.self="openMessageRow\(msg\)"/);
  assert.match(source, /@keydown\.space\.prevent\.self="openMessageRow\(msg\)"/);
  assert.match(source, /async function copyVerificationCode\(msg: Message\)/);
  assert.match(source, /const value = String\(msg\.verification_code \|\| ''\)\.trim\(\)/);
  assert.match(source, /navigator\.clipboard\.writeText\(value\)/);
  assert.match(source, /document\.execCommand\('copy'\)/);
  assert.match(source, /uiStore\.success\('验证码已复制'\);\s*markMessageRead\(msg\);/s);
  assert.match(source, /function markMessageRead\(msg: Message\)/);
  assert.match(source, /if \(noReadStateFolder\.value \|\| msg\.is_read\) return;/);
  assert.match(source, /msg\.is_read = true;/);
  assert.match(source, /api\.post\('\/mark-read', \{[\s\S]*message_id: msg\.id,[\s\S]*folder: mailStore\.currentFolder,[\s\S]*account_id: mailStore\.currentAccountId \|\| '',[\s\S]*\}\)\.catch/s);
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
  assert.match(source, /@media \(max-width:\s*768px\)[\s\S]*\.mail-title-content\s*\{[^}]*flex:\s*1 1 auto;[^}]*min-width:\s*0;/s);
  assert.match(source, /@media \(max-width:\s*768px\)[\s\S]*\.mail-meta-actions\s*\{[^}]*grid-area:\s*meta;[^}]*justify-self:\s*end;[^}]*margin:\s*0;[^}]*gap:\s*6px;/s);
  assert.match(source, /@media \(max-width:\s*768px\)[\s\S]*\.mail-date\s*\{[^}]*grid-area:\s*date;[^}]*width:\s*auto;[^}]*margin:\s*0;[^}]*padding:\s*0;/s);
  assert.match(source, /@media \(max-width:\s*768px\)[\s\S]*\.list-items\s*\{[^}]*overflow-x:\s*hidden;/s);
});

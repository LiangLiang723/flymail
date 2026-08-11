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
  assert.match(source, /@media \(max-width:\s*768px\)[\s\S]*\.verification-code-copy\s*\{[^}]*grid-area:\s*code;[^}]*width:\s*28px;[^}]*min-width:\s*28px;[^}]*height:\s*28px;/s);
  assert.doesNotMatch(source, /@media \(max-width:\s*768px\)[\s\S]*\.verification-code-copy\s*\{[^}]*min-width:\s*72px;/s);
  assert.match(source, /@media \(max-width:\s*768px\)[\s\S]*grid-template-areas:[\s\S]*"select info code"/s);
  assert.match(source, /@media \(max-width:\s*768px\)[\s\S]*\.list-items\s*\{[^}]*overflow-x:\s*hidden;/s);
});

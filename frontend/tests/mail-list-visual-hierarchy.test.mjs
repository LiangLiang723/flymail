import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (file) => readFile(path.join(frontendRoot, file), 'utf8');

test('mail list keeps search on the desktop toolbar and removes duplicate folder counts', async () => {
  const mail = await read('src/views/MailList.vue');

  assert.match(mail, /<span v-else class="list-count">\s*\{\{ mailStore\.currentFolderName \}\}\s*<\/span>/s);
  assert.match(mail, /<div class="toolbar-right">[\s\S]*<MailSearchBar/s);
  assert.match(mail, /\.toolbar-right\s*\{[^}]*flex-wrap:\s*nowrap;[^}]*justify-content:\s*flex-end;/s);
  assert.match(mail, /\.list-count\s*\{[^}]*font-size:\s*13px;[^}]*color:\s*var\(--text-primary\);[^}]*font-weight:\s*var\(--font-semibold\);/s);
});

test('mail row metadata rail owns optional icon spacing before the date', async () => {
  const mail = await read('src/views/MailList.vue');

  assert.match(
    mail,
    /\.mail-meta-actions\s*\{[^}]*display:\s*inline-flex;[^}]*align-items:\s*center;[^}]*flex-shrink:\s*0;[^}]*gap:\s*6px;[^}]*margin-left:\s*8px;/s,
  );
  assert.match(mail, /\.att-badge\s*\{[^}]*width:\s*15px;[^}]*height:\s*15px;[^}]*margin:\s*0;/s);
  assert.match(mail, /\.verification-code-copy\s*\{[^}]*margin:\s*0;/s);
  assert.match(mail, /\.mail-date\s*\{[^}]*width:\s*58px;[^}]*margin-left:\s*8px;[^}]*padding-left:\s*0;/s);
  assert.doesNotMatch(mail, /\.verification-code-copy\s*\{[^}]*margin:\s*0 8px;/s);
});

test('unread rows keep icon and weight without a text status badge', async () => {
  const mail = await read('src/views/MailList.vue');

  assert.doesNotMatch(mail, /class="mail-status-tag"/);
  assert.doesNotMatch(mail, /`未读 \$\{msg\.unread_count\}`/);
  assert.match(mail, /v-if="!noReadStateFolder && !msg\.is_read" class="mail-status-icon unread-icon"/);
  assert.match(mail, /\.mail-item\.unread::before\s*\{[^}]*background:\s*var\(--color-accent\);/s);
  assert.match(mail, /\.mail-item\.unread \.mail-from\s*\{[^}]*font-weight:\s*var\(--font-semibold\);/s);
  assert.match(mail, /\.mail-item\.unread \.mail-subject\s*\{[^}]*font-weight:\s*var\(--font-medium\);/s);
  assert.match(mail, /\.mail-date\s*\{[^}]*width:\s*58px;/s);
  assert.match(mail, /\.mail-sender\s*\{[^}]*width:\s*165px;[^}]*gap:\s*9px;[^}]*padding-right:\s*24px;/s);
  assert.match(mail, /@media \(max-width:\s*1180px\) and \(min-width:\s*769px\)[\s\S]*\.mail-sender\s*\{[^}]*width:\s*150px;[^}]*padding-right:\s*12px;/s);
  assert.match(mail, /@media \(max-width:\s*768px\)[\s\S]*\.mail-sender\s*\{[^}]*width:\s*auto;[^}]*padding-right:\s*0;/s);
  assert.doesNotMatch(mail, /@media \(max-width:\s*768px\)[\s\S]*\.mail-status-icon\s*\{[^}]*display:\s*none;/s);
  assert.match(mail, /border-bottom-color:\s*color-mix\(in srgb, var\(--border-color\) 36%, transparent\);/);
});

test('account is context while folder remains the strong selected navigation item', async () => {
  const sidebar = await read('src/components/app/AppSidebar.vue');
  const css = await read('src/styles/app-shell.css');

  assert.doesNotMatch(sidebar, /active:\s*currentView === 'mail' && mailStore\.currentAccountId === account\.id/);
  assert.match(sidebar, /'is-context': mailStore\.currentAccountId === account\.id/);
  assert.match(sidebar, /active:\s*currentView === 'mail' && mailStore\.currentFolder === folder\.path/);
  assert.match(css, /\.sidebar-account-item\.is-context\s*\{[^}]*background:\s*var\(--ui-fill-muted\);/s);
  assert.doesNotMatch(css, /\.sidebar-account-item\.active \.sidebar-row-icon::before/);
});

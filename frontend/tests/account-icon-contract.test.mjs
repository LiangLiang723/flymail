import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), 'utf8');

test('account icon component prioritizes upload preset and provider fallback', async () => {
  const source = await read('src/components/account/AccountIcon.vue');
  assert.match(source, /account\.icon_type === 'upload'/);
  assert.match(source, /account\.icon_type === 'preset'/);
  assert.match(source, /providerIcon\(props\.account\.provider\)/);
  assert.match(source, /@error=/);
  assert.match(source, /aria-label/);
});

test('account edit offers preset upload crop and restore actions', async () => {
  const source = await read('src/views/AccountList.vue');
  assert.match(source, /AccountIcon/);
  assert.match(source, /AccountIconCropDialog/);
  assert.match(source, /ACCOUNT_ICON_PRESETS/);
  assert.match(source, /accept="image\/jpeg,image\/png,image\/webp"/);
  assert.match(source, /恢复默认图标/);
});

test('all account identity surfaces use the shared account icon component', async () => {
  const files = [
    'src/views/AccountList.vue',
    'src/views/MailList.vue',
    'src/components/app/AppSidebar.vue',
    'src/views/Backup.vue',
    'src/views/UnifiedInbox.vue',
  ];
  for (const file of files) {
    const source = await read(file);
    assert.match(source, /import AccountIcon from/, file);
    assert.match(source, /<AccountIcon/, file);
  }
});

test('shared account icons preserve the legacy slot dimensions', async () => {
  const icon = await read('src/components/account/AccountIcon.vue');
  const accountList = await read('src/views/AccountList.vue');
  const mailList = await read('src/views/MailList.vue');
  const sidebar = await read('src/components/app/AppSidebar.vue');
  const backup = await read('src/views/Backup.vue');

  assert.match(icon, /size\?: 16 \| 18 \| 24 \| 30 \| 36 \| 48/);
  assert.match(icon, /--account-icon-size/);
  assert.match(icon, /account-icon-shell--provider/);
  assert.doesNotMatch(icon, /border:\s*1px solid/);
  assert.doesNotMatch(icon, /box-shadow:/);
  assert.doesNotMatch(icon, /^\.account-icon-svg :deep\(svg\)/m);
  assert.match(icon, /\.account-icon-shell--provider \.account-icon-svg :deep\(svg\)[\s\S]*width:\s*16px/);

  assert.match(accountList, /<AccountIcon[^>]*:size="36"[^>]*decorative/);
  assert.match(accountList, /<AccountIcon[^>]*:size="48"/);
  assert.match(mailList, /<AccountIcon[^>]*:size="18"[^>]*decorative/);
  assert.match(sidebar, /<AccountIcon[^>]*:size="30"[^>]*decorative/);
  assert.match(backup, /<AccountIcon[^>]*:size="16"[^>]*decorative/);
});

test('unified inbox keeps icons inside existing grid cells', async () => {
  const source = await read('src/views/UnifiedInbox.vue');

  assert.match(source, /class="account-option__identity"[\s\S]*<AccountIcon[^>]*:size="30"/);
  assert.match(source, /class="message-account"[\s\S]*<AccountIcon[^>]*:size="16"/);
  assert.doesNotMatch(source, /<span class="read-dot"[^>]*><\/span>\s*<AccountIcon/);
  assert.match(source, /grid-template-columns:\s*20px minmax\(0, 1fr\) auto/);
  assert.match(source, /grid-template-columns:\s*12px minmax\(150px, 220px\) minmax\(0, 1fr\) auto 72px/);
});

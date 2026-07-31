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

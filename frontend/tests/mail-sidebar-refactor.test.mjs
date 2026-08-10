import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (file) => readFile(path.join(frontendRoot, file), 'utf8');
const readOptional = async (file) => {
  try {
    return await read(file);
  } catch (error) {
    if (error?.code === 'ENOENT') return '';
    throw error;
  }
};

test('app shell owns mail navigation without the legacy mail navigation event', async () => {
  const app = await read('src/App.vue');
  const sidebar = await read('src/components/app/AppSidebar.vue');
  const mail = await read('src/views/MailList.vue');

  assert.match(app, /@compose="openComposeFromSidebar"/);
  assert.match(app, /@select-account="openMailAccount"/);
  assert.match(app, /@select-folder="openMailFolder"/);
  assert.match(app, /@reauthorize-account="reauthorizeAccount"/);
  assert.match(sidebar, /class="[^"]*sidebar-compose-action[^"]*"/);
  assert.match(sidebar, /class="sidebar-mail-accounts"/);
  assert.match(sidebar, /class="sidebar-mail-folders"/);
  assert.doesNotMatch(app, /flymail-mail-navigation/);
  assert.doesNotMatch(mail, /flymail-mail-navigation/);
});

test('folder and unified active states are tied to the matching page', async () => {
  const sidebar = await read('src/components/app/AppSidebar.vue');

  assert.match(sidebar, /currentView === 'unified'/);
  assert.match(sidebar, /currentView === 'mail'[^\n]*mailStore\.currentFolder === folder\.path/);
});

test('sidebar keeps primary mail actions fixed around one account and folder scroll owner', async () => {
  const sidebar = await read('src/components/app/AppSidebar.vue');
  const css = await read('src/styles/app-shell.css');

  assert.match(sidebar, /class="sidebar-primary-actions"/);
  assert.match(sidebar, /class="sidebar-scroll sidebar-mail-navigation"/);
  assert.match(sidebar, /id="sidebar-accounts-title"[^>]*>邮箱账号</);
  assert.match(sidebar, /id="sidebar-folders-title"[^>]*>文件夹</);
  assert.match(sidebar, /class="sidebar-row sidebar-compose-action"[\s\S]*:disabled="mailStore\.accounts\.length === 0"/s);
  assert.match(css, /\.sidebar-scroll\s*\{[^}]*flex:\s*1;[^}]*min-height:\s*0;[^}]*overflow-y:\s*auto;/s);
});

test('desktop and mobile share one account and folder navigation tree', async () => {
  const sidebar = await read('src/components/app/AppSidebar.vue');
  const css = await read('src/styles/app-shell.css');

  assert.doesNotMatch(sidebar, /mobile-mail-navigation/);
  assert.doesNotMatch(css, /\.mobile-mail-navigation/);
  assert.match(sidebar, /v-for="account in mailStore\.accounts"/);
  assert.match(sidebar, /v-for="folder in mailStore\.folders"/);
  assert.match(css, /@media \(max-width:\s*960px\)[\s\S]*width:\s*min\(88vw,\s*324px\)/s);
});

test('low-frequency product areas live in the user menu instead of the mail sidebar', async () => {
  const menu = await read('src/components/app/UserMenu.vue');
  const sidebar = await read('src/components/app/AppSidebar.vue');
  const css = await read('src/styles/app-shell.css');

  for (const [key, label] of [
    ['contacts', '联系人'],
    ['history-sync', '同步管理'],
    ['accounts', '账号管理'],
    ['backup', '邮件备份'],
  ]) {
    assert.match(menu, new RegExp(`navigate\\('${key}'\\)[\\s\\S]*${label}`));
  }
  assert.doesNotMatch(sidebar, />联系人</);
  assert.doesNotMatch(sidebar, />同步管理</);
  assert.doesNotMatch(sidebar, />账号管理</);
  assert.doesNotMatch(sidebar, />邮件备份</);
  assert.match(css, /\.user-menu-popover\s*\{[^}]*max-height:\s*calc\(100dvh - 96px\);[^}]*overflow-y:\s*auto;/s);
});

test('mail list no longer owns the desktop account and folder sidebar', async () => {
  const mail = await read('src/views/MailList.vue');

  assert.doesNotMatch(mail, /class="folder-sidebar"/);
  assert.doesNotMatch(mail, /class="account-switcher"/);
  assert.doesNotMatch(mail, /class="compose-entry-btn"/);
  assert.doesNotMatch(mail, /function switchAccount\(/);
  assert.doesNotMatch(mail, /function handleMailNavigation\(/);
  assert.doesNotMatch(mail, /function openCompose\(/);
  assert.doesNotMatch(mail, /flymail-mail-navigation/);
  assert.match(mail, /flymail-toggle-sidebar/);
  assert.match(mail, /grid-template-columns:\s*minmax\(0,\s*1fr\)/);
});

test('sidebar mail actions honor navigation protection before mutating mail context', async () => {
  const app = await read('src/App.vue');

  assert.match(app, /async function openMailAccount\(accountId: string\)[\s\S]*if \(!await requestNavigation\('mail'\)\) return;[\s\S]*mailStore\.setAccount\(accountId\)/s);
  assert.match(app, /async function openMailFolder\(path: string\)[\s\S]*if \(!await requestNavigation\('mail'\)\) return;[\s\S]*mailStore\.setFolder\(path\)/s);
  assert.match(app, /async function openComposeFromSidebar\(\)[\s\S]*if \(!await confirmNavigation\('compose'\)\) return;[\s\S]*mailStore\.clearComposeWorkspace\(\);[\s\S]*mailStore\.setComposeDraft\([\s\S]*commitNavigation\('compose'\);/s);
  assert.match(app, /const authenticatedViews = new Set\(/);
  assert.doesNotMatch(app, /navItems\.value\.some/);
});

test('account reauthorization is shared outside the mail list', async () => {
  const mail = await read('src/views/MailList.vue');
  const composable = await readOptional('src/composables/useAccountReauthorization.ts');

  assert.match(mail, /useAccountReauthorization/);
  assert.doesNotMatch(mail, /async function reauthorize\(/);
  assert.match(composable, /export function useAccountReauthorization\(/);
  assert.match(composable, /async function reauthorizeAccount\(accountId\?: string\)/);
  assert.match(composable, /sessionStorage\.setItem\('flymail_oauth_reauth', '1'\)/);
});

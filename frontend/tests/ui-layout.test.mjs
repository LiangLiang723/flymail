import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

async function readSource(relativePath) {
  return readFile(path.join(frontendRoot, relativePath), 'utf8');
}

test('authentication boot state never renders the login form before session checking finishes', async () => {
  const appSource = await readSource('src/App.vue');
  const loginSource = await readSource('src/views/LoginView.vue');

  assert.match(appSource, /<AuthGate/);
  assert.doesNotMatch(appSource, /!authReady\s*\|\|\s*!currentUser/);
  assert.match(appSource, /authState/);
  assert.match(loginSource, /role="alert"/);
  assert.match(loginSource, /getLoginErrorMessage/);
});

test('application shell uses extracted navigation components without legacy duplicate markup', async () => {
  const appSource = await readSource('src/App.vue');
  const sidebarSource = await readSource('src/components/app/AppSidebar.vue');
  const userMenuSource = await readSource('src/components/app/UserMenu.vue');
  const shellCss = await readSource('src/styles/app-shell.css');

  assert.match(appSource, /<AppSidebar/);
  assert.match(appSource, /<NotificationDrawer/);
  assert.doesNotMatch(appSource, /v-if="false"/);
  assert.doesNotMatch(appSource, /<style scoped>/);
  assert.doesNotMatch(appSource, /class="topbar"/);
  assert.match(sidebarSource, /class="sidebar-header"/);
  assert.match(sidebarSource, /class="sidebar-brand"/);
  assert.match(sidebarSource, /class="nav-list"/);
  assert.match(sidebarSource, /v-for="item in navItems"/);
  assert.doesNotMatch(sidebarSource, /class="nav-group-label"/);
  assert.match(userMenuSource, /class="sidebar-profile-trigger"/);
  assert.match(userMenuSource, /class="user-menu-popover"/);
  assert.match(shellCss, /\.user-menu-popover\s*\{[^}]*position:\s*fixed/s);
});

test('desktop application shell is a viewport grid instead of stacking the sidebar above content', async () => {
  const shellCss = await readSource('src/styles/app-shell.css');

  assert.match(shellCss, /\.app-shell\s*\{[^}]*display:\s*grid;/s);
  assert.match(shellCss, /\.app-shell\s*\{[^}]*height:\s*100(?:d)?vh;/s);
  assert.match(shellCss, /\.app-shell\s*\{[^}]*grid-template-rows:\s*minmax\(0,\s*1fr\)/s);
  assert.match(shellCss, /\.app-shell\s*\{[^}]*overflow:\s*hidden;/s);
  assert.match(shellCss, /\.app-sidebar\s*\{[^}]*grid-column:\s*1;[^}]*grid-row:\s*1;/s);
  assert.match(shellCss, /\.main\s*\{[^}]*grid-column:\s*2;[^}]*grid-row:\s*1;/s);
  assert.match(shellCss, /\.toast-container\s*\{[^}]*position:\s*fixed;/s);
  assert.match(shellCss, /\.notification-overlay,\s*\.confirm-overlay\s*\{[^}]*position:\s*fixed;/s);
  assert.match(shellCss, /@media \(max-width:\s*960px\)[\s\S]*\.main\s*\{[^}]*grid-column:\s*1;/s);
});

test('application overlays keep dense layout and independent scrolling', async () => {
  const shellCss = await readSource('src/styles/app-shell.css');

  assert.match(shellCss, /\.notification-drawer\s*\{[^}]*display:\s*flex;[^}]*flex-direction:\s*column;/s);
  assert.match(shellCss, /\.notification-list\s*\{[^}]*flex:\s*1;[^}]*min-height:\s*0;[^}]*overflow-y:\s*auto;/s);
  assert.match(shellCss, /\.notification-item\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:/s);
  assert.match(shellCss, /\.toast-container \.toast-item\s*\{[^}]*padding:/s);
  assert.match(shellCss, /\.confirm-dialog\s*\{[^}]*padding:/s);
  assert.match(shellCss, /\.confirm-actions\s*\{[^}]*display:\s*flex;/s);
});

test('mail view keeps the toolbar inside the list while rendering a dedicated detail pane', async () => {
  const source = await readSource('src/views/MailList.vue');
  const listStart = source.indexOf('class="mail-list"');
  const toolbarStart = source.indexOf('class="list-toolbar"');

  assert.match(source, /class="folder-sidebar-header"/);
  assert.match(source, /class="account-switcher"/);
  assert.ok(listStart >= 0, 'mail list container should exist');
  assert.ok(toolbarStart > listStart, 'list toolbar should stay inside the mail list container');
  assert.match(source, /v-if="selectedMessage" class="mail-detail"/);
  assert.match(source, /class="mail-detail mail-detail-empty"/);
  assert.doesNotMatch(source, /mail-preview-pane/);
});

test('responsive shell keeps a stable 72px icon rail and uses a mobile drawer', async () => {
  const appSource = await readSource('src/App.vue');
  const sidebarSource = await readSource('src/components/app/AppSidebar.vue');
  const shellCss = await readSource('src/styles/app-shell.css');

  assert.match(appSource, /<AppSidebar/);
  assert.match(sidebarSource, /class="sidebar-icon-rail"/);
  assert.match(sidebarSource, /class="sidebar-label-pane"/);
  assert.match(sidebarSource, /class="mobile-sidebar-backdrop"/);
  assert.match(appSource, /flymail_sidebar_collapsed/);
  assert.match(shellCss, /--app-sidebar-expanded:\s*248px/);
  assert.match(shellCss, /--app-sidebar-collapsed:\s*72px/);
  assert.match(shellCss, /grid-template-columns:\s*72px minmax\(0,\s*1fr\)/);
  assert.doesNotMatch(shellCss, /\.app-shell\.sidebar-collapsed[^\{]*\{[^}]*flex-direction:/s);
  assert.match(sidebarSource, /class="mobile-mail-navigation"/);
  assert.match(sidebarSource, /type: 'reauth'/);
  assert.match(shellCss, /prefers-reduced-transparency/);
});

test('collapsed sidebar swaps one brand slot from logo to expand icon on interaction', async () => {
  const component = await readSource('src/components/app/AppSidebar.vue');
  const css = await readSource('src/styles/app-shell.css');

  assert.match(component, /sidebar-collapsed-toggle/);
  assert.match(component, /sidebar-collapsed-logo/);
  assert.match(component, /sidebar-collapsed-expand/);
  assert.match(css, /\.sidebar-collapsed-toggle\s*\{[^}]*width:\s*44px;[^}]*height:\s*44px/s);
  assert.match(css, /\.sidebar-collapsed-logo\s*\{[^}]*opacity:\s*1/s);
  assert.match(css, /\.sidebar-collapsed-expand\s*\{[^}]*opacity:\s*0/s);
  assert.match(css, /\.sidebar-collapsed-toggle:hover \.sidebar-collapsed-logo,[\s\S]*\.sidebar-collapsed-toggle:focus-visible \.sidebar-collapsed-logo\s*\{[^}]*opacity:\s*0/s);
  assert.match(css, /\.sidebar-collapsed-toggle:hover \.sidebar-collapsed-expand,[\s\S]*\.sidebar-collapsed-toggle:focus-visible \.sidebar-collapsed-expand\s*\{[^}]*opacity:\s*1/s);
  assert.match(css, /--sidebar-item-icon-column:\s*56px/);
  assert.match(css, /\.nav-item\s*\{[^}]*grid-template-columns:\s*var\(--sidebar-item-icon-column\) minmax\(0,\s*1fr\)/s);
  assert.match(css, /\.app-shell\.sidebar-collapsed \.sidebar-profile-trigger\s*\{[^}]*grid-template-columns:\s*var\(--sidebar-item-icon-column\) 0 0/s);
});

test('mobile mail view delegates account and folder navigation without horizontal overflow', async () => {
  const source = await readSource('src/views/MailList.vue');

  assert.doesNotMatch(source, /mobile-account-tabs/);
  assert.match(source, /flymail-mail-navigation/);
  assert.match(source, /function handleMailNavigation/);
  assert.match(source, /detail\.type === 'reauth'/);
  assert.match(source, /@media \(max-width: 768px\)[\s\S]*\.mail-item,[\s\S]*min-width: 0;/);
});

test('destructive secondary actions and attachment controls use explicit variants', async () => {
  const notificationSource = await readSource('src/views/NotificationSettings.vue');
  const mailSource = await readSource('src/views/MailList.vue');
  const componentCss = await readSource('src/styles/components.css');

  assert.match(notificationSource, /class="btn btn-danger-ghost"/);
  assert.doesNotMatch(notificationSource, /class="btn btn-secondary danger"/);
  assert.match(mailSource, /class="attachment-action"/);
  assert.doesNotMatch(mailSource, /class="att-download"/);
  assert.match(componentCss, /\.btn-danger-ghost\s*\{[^}]*color:\s*var\(--ui-danger\);/s);
  assert.match(componentCss, /\.attachment-action\s*\{[^}]*border:\s*1px solid transparent;[^}]*background:\s*transparent;/s);
  assert.match(componentCss, /\.attachment-action:disabled\s*\{/);
});

test('audited compact controls keep readable colors, labels and usable hit areas', async () => {
  const mailSource = await readSource('src/views/MailList.vue');
  const backupSource = await readSource('src/views/Backup.vue');
  const composeSource = await readSource('src/views/ComposeEmail.vue');
  const accountSource = await readSource('src/views/AccountList.vue');

  assert.match(mailSource, /\.filter-btn\s*\{[^}]*color:\s*var\(--text-secondary\);/s);
  assert.match(mailSource, /class="btn-icon mobile-filter-toggle"[^>]*aria-label="筛选邮件"/s);
  assert.match(backupSource, /class="btn-icon mobile-filter-toggle"[^>]*aria-label="筛选备份邮件"/s);
  assert.match(composeSource, /\.text-btn\s*\{[^}]*min-width:\s*var\(--ui-control-md\);/s);
  assert.match(composeSource, /\.text-btn\s*\{[^}]*padding:\s*0 6px;/s);
  assert.match(accountSource, /class="toggle-switch"[^>]*aria-label="获取历史邮件"[^>]*:aria-pressed="fetchHistory"/s);
  assert.match(accountSource, /class="toggle-switch"[^>]*aria-label="隐藏邮箱地址"[^>]*:aria-pressed="editForm\.hide_email"/s);
  assert.match(accountSource, /\.toggle-switch\s*\{[^}]*width:\s*48px;[^}]*height:\s*28px;/s);
});

test('editor popovers preserve accessible names and compact touch targets', async () => {
  const editorSource = await readSource('src/components/TiptapEditor.vue');
  const composeSource = await readSource('src/views/ComposeEmail.vue');

  assert.match(editorSource, /class="color-swatch"[^>]*:aria-label="`使用颜色 \$\{c\}`"/s);
  assert.match(editorSource, /\.color-swatch\s*\{[^}]*width:\s*28px;[^}]*height:\s*28px;/s);
  assert.match(editorSource, /\.color-swatch\s*\{[^}]*border:\s*2px solid var\(--ui-border-strong\);/s);
  assert.match(editorSource, /\.emoji-tab\s*\{[^}]*min-height:\s*32px;/s);
  assert.match(composeSource, /<UiButton[^>]*variant="primary"[^>]*:loading="sending"[^>]*@click="sendMail"/s);
  assert.match(composeSource, /<UiButton[^>]*:loading="savingDraft"[^>]*@click="saveDraft"/s);
  assert.match(composeSource, /\.sig-customize-btn\s*\{[^}]*width:\s*28px;[^}]*height:\s*28px;/s);
  assert.match(composeSource, /\.sig-customize-btn\s*\{[^}]*color:\s*var\(--ui-text-2\);/s);
});

test('data-dependent remove and preview controls keep explicit names and usable sizes', async () => {
  const composeSource = await readSource('src/views/ComposeEmail.vue');
  const settingsSource = await readSource('src/views/Settings.vue');

  assert.match(composeSource, /class="tag-remove"[^>]*:aria-label="`移除收件人 \$\{addr\}`"/s);
  assert.match(composeSource, /class="tag-remove"[^>]*:aria-label="`移除抄送人 \$\{addr\}`"/s);
  assert.match(composeSource, /class="tag-remove"[^>]*:aria-label="`移除密送人 \$\{addr\}`"/s);
  assert.match(composeSource, /class="att-remove"[^>]*:aria-label="`移除附件 \$\{att\.filename\}`"/s);
  assert.match(composeSource, /\.sig-delete-btn\s*\{[^}]*width:\s*28px;[^}]*height:\s*28px;/s);
  assert.match(composeSource, /\.sig-delete-btn:focus-visible\s*\{\s*opacity:\s*1;/s);
  assert.match(settingsSource, /class="img-preview-close"[^>]*aria-label="关闭图片预览"/s);
});

test('manual refresh animates only while the latest page request is active', async () => {
  const source = await readSource('src/views/MailList.vue');
  const spinner = await readSource('src/components/ui/UiSpinner.vue');
  const styles = await readSource('src/styles/components.css');

  assert.match(source, /<UiIconButton[\s\S]*class="refresh-button"/);
  assert.match(source, /:loading="refreshingLatest"/);
  assert.match(source, /const refreshingLatest = ref\(false\)/);
  assert.match(source, /refreshingLatest\.value = true/);
  assert.match(source, /finally \{\s*refreshingLatest\.value = false;/s);
  assert.match(spinner, /ui-spinner/);
  assert.match(styles, /\.ui-spinner,[\s\S]*\{[^}]*animation:\s*ui-spin 0\.72s linear infinite/s);
  assert.match(styles, /@media \(prefers-reduced-motion:\s*reduce\)[\s\S]*\.ui-spinner,[\s\S]*\{[^}]*animation:\s*none/s);
});

import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

async function readSource(relativePath) {
  return readFile(path.join(frontendRoot, relativePath), 'utf8');
}

test('sync cards keep one primary action and move secondary actions into an accessible menu', async () => {
  const source = await readSource('src/views/HistorySync.vue');

  assert.doesNotMatch(source, /<button v-else class="btn btn-secondary" disabled>暂停\/继续<\/button>/);
  assert.match(source, /class="job-more-trigger"[^>]*aria-label="更多操作"/s);
  assert.match(source, /class="job-action-menu"[^>]*role="menu"/s);
  assert.match(source, /role="menuitem"[^>]*[\s\S]*?刷新同步/);
  assert.match(source, /role="menuitem"[^>]*[\s\S]*?清空同步数据/);
  assert.match(source, /const openActionMenuId = ref<string \| null>\(null\)/);
  assert.match(source, /function toggleActionMenu\(accountId: string\)/);
  assert.match(source, /function runActionMenuCommand\(command: \(\) => void \| Promise<void>\)/);
  assert.match(source, /function handleActionMenuPointerDown\(event: PointerEvent\)/);
  assert.match(source, /function handleActionMenuKeydown\(event: KeyboardEvent\)/);
  assert.match(source, /event\.key === 'Escape'/);
  assert.match(source, /window\.addEventListener\('pointerdown', handleActionMenuPointerDown\)/);
  assert.match(source, /window\.removeEventListener\('pointerdown', handleActionMenuPointerDown\)/);
});

test('sync card menu exposes global account ordering actions', async () => {
  const source = await readSource('src/views/HistorySync.vue');

  assert.match(source, />\s*置顶\s*</);
  assert.match(source, />\s*上移\s*</);
  assert.match(source, />\s*下移\s*</);
  assert.match(source, /const mailStore = useMailStore\(\)/);
  assert.match(source, /const orderSaving = ref\(false\)/);
  assert.match(source, /async function moveAccount\(accountId: string, direction: 'top' \| 'up' \| 'down'\)/);
  assert.match(source, /const savePromise = mailStore\.saveAccountOrder\(nextIds\)/);
  assert.match(source, /jobs\.value = orderJobs\(jobs\.value\)/);
  assert.match(source, /isFirstAccount\(item\.account_id\)/);
  assert.match(source, /isLastAccount\(item\.account_id\)/);
});

test('sidebar no longer receives or renders an application version', async () => {
  const app = await readSource('src/App.vue');
  const sidebar = await readSource('src/components/app/AppSidebar.vue');
  const shellCss = await readSource('src/styles/app-shell.css');

  assert.doesNotMatch(app, /:app-version="appVersion"/);
  assert.doesNotMatch(app, /const appVersion/);
  assert.doesNotMatch(sidebar, /class="sidebar-version/);
  assert.doesNotMatch(sidebar, /appVersion: string/);
  assert.doesNotMatch(shellCss, /\.sidebar-version\s*\{/);
});

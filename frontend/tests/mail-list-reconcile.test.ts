import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

let reconcileMessagePage: undefined | ((current: Array<Record<string, unknown>>, incoming: Array<Record<string, unknown>>) => Array<Record<string, unknown>>);
try {
  const module = await import('../src/utils/mail-list-reconcile.ts');
  reconcileMessagePage = module.reconcileMessagePage;
} catch {
  reconcileMessagePage = undefined;
}

test('reconciles a refreshed page by inserting new mail and updating existing rows', () => {
  assert.equal(typeof reconcileMessagePage, 'function');

  const current = [
    { id: 'mail-3', subject: '旧主题', is_read: false },
    { id: 'mail-2', subject: '保留邮件', is_read: true },
    { id: 'mail-1', subject: '移出当前页', is_read: true },
  ];
  const incoming = [
    { id: 'mail-4', subject: '新邮件', is_read: false },
    { id: 'mail-3', subject: '新主题', is_read: true },
    { id: 'mail-2', subject: '保留邮件', is_read: true },
  ];

  const result = reconcileMessagePage!(current, incoming);

  assert.deepEqual(result.map((message) => message.id), ['mail-4', 'mail-3', 'mail-2']);
  assert.deepEqual(result[1], { id: 'mail-3', subject: '新主题', is_read: true });
  assert.equal(result.some((message) => message.id === 'mail-1'), false);
});

test('manual refresh applies the returned page without an empty-list reload', async () => {
  const source = await readFile(new URL('../src/views/MailList.vue', import.meta.url), 'utf8');
  const refreshBlock = source.match(/async function refreshLatestPage\(\)[\s\S]*?\n}\n\n\/\*\* 重新授权/)?.[0] || '';

  assert.match(source, /import \{ reconcileMessagePage \} from '\.\.\/utils\/mail-list-reconcile';/);
  assert.match(source, /function applyMessagePage\(data: any, reconcileVisible = false\)/);
  assert.match(source, /messages\.value\.splice\(0, messages\.value\.length, \.\.\.reconciled\)/);
  assert.match(refreshBlock, /const refreshData = await api\.get\('\/messages\/refresh', \{ params \}\) as any;/);
  assert.match(refreshBlock, /applyMessagePage\(refreshData, true\)/);
  assert.doesNotMatch(refreshBlock, /await loadMessages\(\);/);
});

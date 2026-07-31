import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import { createPinia, setActivePinia } from 'pinia';
import { createServer } from 'vite';

function createSessionStorage() {
  const values = new Map();
  return {
    getItem(key) {
      return values.has(key) ? values.get(key) : null;
    },
    setItem(key, value) {
      values.set(key, String(value));
    },
    removeItem(key) {
      values.delete(key);
    },
    clear() {
      values.clear();
    },
  };
}

test('patches account icon fields in memory and session storage', async () => {
  globalThis.sessionStorage = createSessionStorage();
  sessionStorage.setItem('flymail_accounts', JSON.stringify([
    { id: 'account-1', provider: 'custom', email: 'user@example.com', icon_type: 'default', icon_value: '', icon_url: '' },
  ]));
  const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  const server = await createServer({
    root: frontendRoot,
    appType: 'custom',
    server: { middlewareMode: true },
    logLevel: 'silent',
  });

  try {
    setActivePinia(createPinia());
    const { useMailStore } = await server.ssrLoadModule('/src/stores/mail.ts');
    const store = useMailStore();
    store.patchAccount('account-1', {
      icon_type: 'preset',
      icon_value: 'work',
      icon_url: '',
    });

    assert.equal(store.accounts[0].icon_type, 'preset');
    assert.equal(store.accounts[0].icon_value, 'work');
    assert.equal(JSON.parse(sessionStorage.getItem('flymail_accounts'))[0].icon_value, 'work');
  } finally {
    await server.close();
  }
});

test('loading accounts also discovers custom folders for the initial account', async () => {
  globalThis.sessionStorage = createSessionStorage();
  const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  const server = await createServer({
    root: frontendRoot,
    appType: 'custom',
    server: { middlewareMode: true },
    logLevel: 'silent',
  });

  try {
    const apiModule = await server.ssrLoadModule('/src/utils/api.ts');
    apiModule.default.get = async (url) => {
      if (url === '/accounts') {
        return { accounts: [{ id: 'account-1', provider: 'custom', email: 'user@example.com' }] };
      }
      if (url === '/folders') {
        return {
          folders: [
            { name: '收件箱', path: 'INBOX' },
            { name: '已发送', path: 'Sent Messages' },
            { name: '草稿箱', path: 'Drafts' },
            { name: '垃圾邮件', path: 'Junk' },
            { name: '已删除', path: 'Trash' },
            { name: 'OA', path: 'OA' },
            { name: 'ROVO', path: 'ROVO' },
          ],
        };
      }
      if (url === '/folder-counts') {
        return {
          counts: {
            INBOX: { total: 10, unread: 2 },
            OA: { total: 8, unread: 3 },
            ROVO: { total: 6, unread: 4 },
          },
        };
      }
      throw new Error(`unexpected GET ${url}`);
    };

    setActivePinia(createPinia());
    const { useMailStore } = await server.ssrLoadModule('/src/stores/mail.ts');
    const store = useMailStore();

    await store.loadAccounts();

    assert.deepEqual(
      store.folders.map((folder) => folder.name),
      ['收件箱', '已发送', '草稿箱', '垃圾邮件', '已删除', 'OA', 'ROVO'],
    );
  } finally {
    await server.close();
  }
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import {
  buildNavigationModel,
  createNavigationState,
  navigationLocation,
  patchNavigationBadge,
} from '../../src/features/navigation/navigation-state.ts';
import type { NavigationAccount } from '../../src/entities/account/types.ts';

const accounts: NavigationAccount[] = [
  {
    id: 'gmail-1', providerKey: 'gmail', displayName: '工作邮箱', email: 'work@example.com', status: 'active',
    semanticMailboxes: [{ key: 'inbox', name: '收件箱', unreadCount: 3 }],
    nativeLabels: [{ key: '项目/Alpha', name: '项目 Alpha', unreadCount: 2 }],
  },
  {
    id: 'imap-1', providerKey: 'generic', displayName: '个人邮箱', email: 'me@example.net', status: 'auth_required',
    semanticMailboxes: [{ key: 'inbox', name: 'Inbox', unreadCount: 1 }], nativeLabels: [],
  },
];

test('semantic folders are unified once while native labels remain under account', () => {
  const model = buildNavigationModel(accounts, [{ id: 'saved-1', name: '重要客户' }]);
  assert.equal(model.semantic.filter((item) => item.key === 'inbox').length, 1);
  assert.equal(model.accounts[0].nativeLabels[0].key, '项目/Alpha');
  assert.equal(model.accounts[1].action, 'reauthorize');
});

test('navigation locations are typed and encode native keys through route params', () => {
  assert.deepEqual(navigationLocation({ kind: 'semantic', key: 'inbox' }), {
    name: 'mail', params: { scope: 'semantic', key: 'inbox' },
  });
  assert.deepEqual(navigationLocation({ kind: 'native', accountId: 'gmail-1', key: '项目/Alpha' }), {
    name: 'mail', params: { scope: 'native', key: 'gmail-1' }, query: { label: '项目/Alpha' },
  });
});

test('drawer closes after selection, restores focus and expansion serializes safely', () => {
  let focused = 0;
  const state = createNavigationState({ expandedAccountIds: ['gmail-1'], restoreFocus: () => { focused += 1; } });
  state.openDrawer();
  state.select({ kind: 'semantic', key: 'inbox' });
  assert.equal(state.drawerOpen, false);
  assert.equal(focused, 1);
  state.toggleAccount('imap-1');
  assert.deepEqual(state.preference(), { expanded_account_ids: ['gmail-1', 'imap-1'] });
});

test('badge patch updates one navigation projection without bootstrap reload', () => {
  const model = buildNavigationModel(accounts, []);
  const next = patchNavigationBadge(model, { accountId: 'gmail-1', key: '项目/Alpha', unreadCount: 7 });
  assert.equal(next.accounts[0].nativeLabels[0].unreadCount, 7);
  assert.equal(next.accounts[1], model.accounts[1]);
});

test('navigation components use accessible controls and never raw mailbox html', async () => {
  const panel = await readFile(new URL('../../src/features/navigation/NavigationPanel.vue', import.meta.url), 'utf8');
  const account = await readFile(new URL('../../src/features/navigation/AccountSection.vue', import.meta.url), 'utf8');
  const drawer = await readFile(new URL('../../src/features/navigation/MobileNavigationDrawer.vue', import.meta.url), 'utf8');
  assert.match(panel, /aria-label="邮箱导航"/);
  assert.match(account, /aria-expanded/);
  assert.match(drawer, /role="dialog"/);
  assert.doesNotMatch(`${panel}${account}${drawer}`, /v-html/);
});

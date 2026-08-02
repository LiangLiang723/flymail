import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import {
  buildNavigationModel,
  createNavigationState,
  navigationLocation,
  patchNavigationBadge,
  toNavigationAccounts,
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

test('bootstrap account and navigation projections map to usable navigation models', () => {
  const mapped = toNavigationAccounts(
    [{
      id: 'gmail-1', provider_key: 'gmail', email: 'work@example.com', display_name: '工作邮箱',
      remark: '', group_name: '', status: 'active', include_in_unified: true,
      runtime_status: 'normal', idle_status: 'connected', icon_mode: 'provider', icon_value: '',
      icon_object_sha256: null, total_count: 10, unread_count: 3,
    }],
    [{
      account_id: 'gmail-1',
      semantic_mailboxes: [{ id: 'mbx-inbox', semantic_key: 'inbox', native_key: 'INBOX', native_name: '收件箱', total_count: 10, unread_count: 3, sync_status: 'ready' }],
      native_labels: [{ id: 'mbx-alpha', semantic_key: 'all_mail', native_key: '项目/Alpha', native_name: '项目 Alpha', total_count: 2, unread_count: 2, sync_status: 'ready' }],
    }],
  );
  assert.equal(mapped[0].semanticMailboxes[0].key, 'inbox');
  assert.equal(mapped[0].nativeLabels[0].key, 'mbx-alpha');
  assert.equal(mapped[0].nativeLabels[0].semanticKey, 'all_mail');
});

test('navigation locations are typed and encode native mailbox ids through route state', () => {
  assert.deepEqual(navigationLocation({ kind: 'semantic', key: 'inbox' }), {
    name: 'mail', params: { scope: 'semantic', key: 'inbox' },
  });
  assert.deepEqual(navigationLocation({ kind: 'native', accountId: 'gmail-1', key: 'mbx-alpha', semanticKey: 'all_mail' }), {
    name: 'mail', params: { scope: 'native', key: 'gmail-1' }, query: { label: 'mbx-alpha', mailbox: 'all_mail' },
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

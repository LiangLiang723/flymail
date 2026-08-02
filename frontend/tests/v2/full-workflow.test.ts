import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import { createBootstrapController } from '../../src/app/bootstrap.ts';
import type { BootstrapResponse, ThreadProjection } from '../../src/shared/api/generated.ts';
import {
  buildNavigationModel,
  createNavigationState,
  navigationLocation,
  toNavigationAccounts,
} from '../../src/features/navigation/navigation-state.ts';
import { ThreadCursorMemory, createThreadQueryKey } from '../../src/features/threads/thread-query.ts';
import { BodyRequestRegistry } from '../../src/features/message-viewer/body-state.ts';
import {
  OperationCommandAdapter,
  canPermanentlyDelete,
  undoRemainingMs,
} from '../../src/features/operations/operation-actions.ts';
import {
  AutosaveController,
  chooseInitialIdentity,
  createComposeModel,
  scheduleToEpochSeconds,
} from '../../src/features/compose/compose-state.ts';
import {
  SearchController,
  appendSearchResults,
  deserializeSearchFilters,
  serializeSearchFilters,
} from '../../src/features/search/search-state.ts';
import { configuredSecret, clearSecretPayload } from '../../src/features/notifications/notification-state.ts';
import { quotaDecreaseNeedsCleanup } from '../../src/features/settings/settings-state.ts';
import { clearSecretAfter, restoreReviewItems } from '../../src/features/backup/backup-state.ts';
import { RealtimeClient } from '../../src/shared/realtime/client.ts';

function bootstrapFixture(): BootstrapResponse {
  return {
    user: { id: 'usr-1', username: 'alice', role: 'admin', enabled: true, nickname: 'Alice', avatar_object_sha256: null },
    permissions: ['mail.read', 'mail.send', 'settings.manage', 'users.manage'],
    accounts: [
      {
        id: 'acc-work', provider_key: 'gmail', email: 'work@example.com', display_name: '工作邮箱',
        remark: '', group_name: '', status: 'active', include_in_unified: true,
        runtime_status: 'normal', idle_status: 'connected', icon_mode: 'provider', icon_value: '',
        icon_object_sha256: null, total_count: 30, unread_count: 4,
      },
      {
        id: 'acc-personal', provider_key: 'custom_imap', email: 'me@example.net', display_name: '个人邮箱',
        remark: '', group_name: '', status: 'auth_required', include_in_unified: false,
        runtime_status: 'auth_required', idle_status: 'disconnected', icon_mode: 'preset', icon_value: 'personal',
        icon_object_sha256: null, total_count: 8, unread_count: 2,
      },
    ],
    navigation: {
      unified: { account_ids: ['acc-work'], total_count: 20, unread_count: 4 },
      accounts: [
        {
          account_id: 'acc-work',
          semantic_mailboxes: [
            { id: 'mbx-inbox', semantic_key: 'inbox', native_key: 'INBOX', native_name: '收件箱', total_count: 20, unread_count: 4, sync_status: 'ready' },
            { id: 'mbx-sent', semantic_key: 'sent', native_key: '[Gmail]/Sent Mail', native_name: '已发送', total_count: 10, unread_count: 0, sync_status: 'ready' },
          ],
          native_labels: [
            { id: 'mbx-alpha', semantic_key: 'all_mail', native_key: '项目/Alpha', native_name: '项目 Alpha', total_count: 3, unread_count: 2, sync_status: 'ready' },
          ],
        },
        { account_id: 'acc-personal', semantic_mailboxes: [], native_labels: [] },
      ],
    },
    ui_preferences: { theme: 'system', density: 'compact', expanded_account_ids: ['acc-work'] },
    sync_alert_summary: { auth_required_accounts: 1, degraded_accounts: 0, pending_accounts: 0, unread_notifications: 2 },
    csrf_token: 'csrf-only-in-memory', realtime_cursor: 4, version: '0.0.25',
  };
}

function thread(id: string, latestAt: number, unread = 0): ThreadProjection {
  return {
    id, subject: `Subject ${id}`, snippet: 'cached snippet', participants: [{ address: 'sender@example.com' }],
    latest_at: latestAt, unread_count: unread, message_count: 1, is_starred: false,
    has_attachments: false, account_ids: ['acc-work'], pending_state: null, operation_status: null,
  };
}

function draft(version = 1) {
  return {
    id: 'draft-1', account_id: 'acc-work', identity_id: 'ident-work', thread_id: null,
    reply_to_message_id: null, subject: '', body_html: '', body_text: '',
    recipients: { to: [], cc: [], bcc: [] }, attachments: [], version,
    status: 'draft', send_state: 'idle', scheduled_at: null, send_message_id: '<stable@example.com>',
    created_at: 1, updated_at: 1, queued_at: null, sent_at: null,
  };
}

test('desktop and mobile bootstrap navigation pagination and recovery stay contract-exact', async () => {
  let bootstrapCalls = 0;
  const bootstrap = createBootstrapController(async () => {
    bootstrapCalls += 1;
    return bootstrapFixture();
  });
  const first = await bootstrap.load();
  const second = await bootstrap.load();
  assert.deepEqual(first, second);
  assert.equal(bootstrapCalls, 1);
  assert.deepEqual(first?.ui_preferences.expanded_account_ids, ['acc-work']);

  const accounts = toNavigationAccounts(first!.accounts, first!.navigation.accounts);
  const navigation = buildNavigationModel(accounts, [{ id: 'saved-important', name: '重要客户' }]);
  assert.equal(navigation.semantic.find((item) => item.key === 'inbox')?.unreadCount, 4);
  assert.equal(navigation.accounts[1].action, 'reauthorize');
  assert.deepEqual(
    navigationLocation({ kind: 'native', accountId: 'acc-work', key: 'mbx-alpha', semanticKey: 'all_mail' }),
    { name: 'mail', params: { scope: 'native', key: 'acc-work' }, query: { label: 'mbx-alpha', mailbox: 'all_mail' } },
  );

  let restored = 0;
  const mobile = createNavigationState({
    expandedAccountIds: first!.ui_preferences.expanded_account_ids,
    restoreFocus: () => { restored += 1; },
  });
  mobile.openDrawer();
  mobile.select({ kind: 'semantic', key: 'inbox' });
  assert.equal(mobile.drawerOpen, false);
  assert.equal(restored, 1);

  const memory = new ThreadCursorMemory(4);
  const key = createThreadQueryKey(first!.user.id, { mailbox: 'inbox' });
  memory.set(key, { threads: [thread('t3', 3, 1), thread('t2', 2)], next_cursor: 'cursor-2' });
  const appended = memory.set(key, { threads: [thread('t2', 2), thread('t1', 1)], next_cursor: null }, true);
  assert.deepEqual(appended.threads.map((item) => item.id), ['t3', 't2', 't1']);
});

test('mail body operations compose search and realtime resume form one local-first workflow', async () => {
  let bodyCalls = 0;
  let releaseBody: ((value: { message_id: string; state: 'queued'; task_id: string }) => void) | undefined;
  const bodies = new BodyRequestRegistry(async (messageId) => {
    bodyCalls += 1;
    return new Promise((resolve) => { releaseBody = resolve as typeof releaseBody; });
  });
  const bodyOne = bodies.request('message-1');
  const bodyTwo = bodies.request('message-1');
  assert.equal(bodyOne, bodyTwo);
  releaseBody?.({ message_id: 'message-1', state: 'queued', task_id: 'job-body-1' });
  assert.equal((await bodyOne).state, 'queued');
  assert.equal(bodyCalls, 1);

  let projection = thread('t3', 3, 1);
  const operations = new OperationCommandAdapter({
    idempotencyKey: () => 'idem-read-1',
    submit: async (command) => ({
      operation_group_id: 'group-1', operation_ids: ['op-1'],
      projection: { ...projection, unread_count: command.operation_type === 'set_read' ? 0 : projection.unread_count },
      undo_token: 'undo-1', undo_expires_at: 1_700_000_010,
    }),
    patchProjection: (_id, next) => { projection = next; },
    fetchAuthoritativeProjection: async () => projection,
  });
  const accepted = await operations.execute({ target_type: 'thread', target_id: 't3', operation_type: 'set_read', desired_state: { read: true } });
  assert.equal(projection.unread_count, 0);
  assert.equal(accepted.operation_ids[0], 'op-1');
  assert.equal(undoRemainingMs(1_700_000_010, 1_700_000_000_000), 10_000);
  assert.equal(canPermanentlyDelete('Subject t3', 'Subject t3'), true);

  const identities = [
    { id: 'ident-work', accountId: 'acc-work', isDefault: true, signatureHtml: '<p>Work</p>' },
    { id: 'ident-personal', accountId: 'acc-personal', signatureHtml: '<p>Personal</p>' },
  ];
  assert.equal(chooseInitialIdentity(identities, 'acc-personal'), 'ident-personal');
  let saveAttempt = 0;
  const autosave = new AutosaveController({
    initial: createComposeModel(draft()),
    debounceMs: 0,
    save: async (model) => {
      saveAttempt += 1;
      if (saveAttempt === 1) {
        throw { status: 409, data: { error: { code: 'draft_version_conflict', details: { current: { ...draft(4), subject: 'remote copy' } } } } };
      }
      return { ...draft(5), subject: model.subject };
    },
  });
  autosave.update({ subject: 'local copy' });
  await assert.rejects(autosave.flush());
  assert.equal(autosave.state, 'conflict');
  autosave.resolveConflict('local');
  await autosave.waitForIdle();
  assert.equal(autosave.model.subject, 'local copy');
  assert.equal(autosave.model.version, 5);
  assert.equal(scheduleToEpochSeconds('2026-08-02T15:30', 540), Date.parse('2026-08-02T06:30:00Z') / 1000);

  const filterQuery = serializeSearchFilters({ keyword: 'invoice', account_ids: ['acc-work'], has_attachment: true });
  assert.deepEqual(deserializeSearchFilters(filterQuery), { keyword: 'invoice', account_ids: ['acc-work'], has_attachment: true });
  const search = new SearchController(async (_filters, signal, cursor) => {
    assert.equal(signal.aborted, false);
    return cursor
      ? { items: [{ thread_id: 't3', matched_message_id: 'm-new', matched_field: 'body', subject: 'Invoice', snippet: 'new', received_at: 3, account_ids: ['acc-work'], unread: false, starred: false, has_attachment: true }], next_cursor: null, fulltext_parser: 'ngram' }
      : { items: [{ thread_id: 't3', matched_message_id: 'm-old', matched_field: 'subject', subject: 'Invoice', snippet: 'old', received_at: 2, account_ids: ['acc-work'], unread: false, starred: false, has_attachment: true }], next_cursor: 'next', fulltext_parser: 'ngram' };
  }, 0);
  const searchPage = await search.search({ keyword: 'invoice' });
  const nextPage = await search.search({ keyword: 'invoice' }, searchPage.next_cursor);
  assert.equal(appendSearchResults(searchPage.items, nextPage.items)[0].matched_message_id, 'm-new');

  const events: string[] = [];
  const realtime = new RealtimeClient({
    initialSequence: 4,
    fetchBacklog: async () => ({
      events: [{ sequence: 5, event_type: 'message.body_state', aggregate_id: 'message-1', occurred_at: 5, payload: { message_id: 'message-1', state: 'ready' } }],
      current_sequence: 5,
    }),
    handlers: {
      invalidateBody: (id) => events.push(`body:${id}`),
      patchThread: (id) => events.push(`thread:${id}`),
      invalidateThread: (id) => events.push(`detail:${id}`),
      statusFallback: () => events.push('fallback'),
    },
  });
  await realtime.handleEnvelope({
    type: 'events',
    current_sequence: 6,
    events: [{ sequence: 6, event_type: 'thread.updated', aggregate_id: 't3', occurred_at: 6, payload: { thread_id: 't3', projection: { unread_count: 0 } } }],
  });
  assert.deepEqual(events, ['body:message-1', 'thread:t3', 'detail:t3']);
  assert.equal(realtime.sequence, 6);
  realtime.handleClose(1006);
  assert.equal(events.at(-1), 'fallback');
  realtime.destroy();
});

test('management notification backup PWA and failure boundaries remain secret-free and server-owned', async () => {
  assert.equal(configuredSecret(true, 'never-return-this-value'), '已配置');
  assert.deepEqual(clearSecretPayload({ token: '', endpoint_url: 'https://notify.example/hook' }, ['token']), { endpoint_url: 'https://notify.example/hook' });
  assert.equal(quotaDecreaseNeedsCleanup(5 * 1024 ** 3, 2 * 1024 ** 3), true);
  assert.deepEqual(
    restoreReviewItems({ pending_sends: 2, pending_remote_operations: 1 }).map((item) => [item.kind, item.state, item.automatic]),
    [['pending_send', 'review_required', false], ['remote_operation', 'review_required', false]],
  );
  let password = 'independent-backup-password';
  const seen: string[] = [];
  await clearSecretAfter(() => password, (value) => { password = value; }, async (secret) => { seen.push(secret); return 'ok'; });
  assert.deepEqual(seen, ['independent-backup-password']);
  assert.equal(password, '');

  const sources = await Promise.all([
    readFile(new URL('../../src/features/accounts/AccountsPage.vue', import.meta.url), 'utf8'),
    readFile(new URL('../../src/features/notifications/NotificationSettingsPage.vue', import.meta.url), 'utf8'),
    readFile(new URL('../../src/features/admin/AdminPage.vue', import.meta.url), 'utf8'),
    readFile(new URL('../../src/features/backup/BackupPage.vue', import.meta.url), 'utf8'),
    readFile(new URL('../../src/features/about/AboutPage.vue', import.meta.url), 'utf8'),
    readFile(new URL('../../src/features/compose/DraftAttachments.vue', import.meta.url), 'utf8'),
    readFile(new URL('../../src/features/compose/ServerPathPicker.vue', import.meta.url), 'utf8'),
    readFile(new URL('../../public/flymail-sw.js', import.meta.url), 'utf8'),
    readFile(new URL('../../src/app/AppV2.vue', import.meta.url), 'utf8'),
    readFile(new URL('../../src/app/error-boundary.ts', import.meta.url), 'utf8'),
    readFile(new URL('../../src/shared/api/errors.ts', import.meta.url), 'utf8'),
  ]);
  const joined = sources.join('\n');
  for (const contract of [
    '/api/v2/accounts/', '/credentials', '/identities/', '/api/v2/notification-channels',
    '/api/v2/notification-rules', '/api/v2/notification-publishers', '/api/v2/admin/users',
    '/api/v2/admin/backups', '/restore-rehearsal', '/api/v2/version', 'OPENAPI_SHA256', 'XMLHttpRequest',
    'root_id', 'relative_path', 'networkFirstNavigation', 'network_error',
  ]) assert.match(joined, new RegExp(contract.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  assert.doesNotMatch(joined, /localStorage|sessionStorage|indexedDB/);
  assert.doesNotMatch(joined, /restore_apply|\/api\/v2\/admin\/restore/);
});

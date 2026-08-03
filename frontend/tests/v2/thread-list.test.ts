import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import type { ThreadListItemResponse, ThreadProjection } from '../../src/shared/api/generated.ts';
import {
  ThreadCursorMemory,
  ThreadListController,
  appendThreadPage,
  createThreadQueryKey,
  patchThreadProjection,
} from '../../src/features/threads/thread-query.ts';
import { createThreadSelection, shouldHandleListShortcut } from '../../src/features/threads/thread-selection.ts';

function thread(id: string, unread = false): ThreadProjection {
  return {
    id, subject: `Subject ${id}`, latest_at: 1, unread_count: unread ? 1 : 0,
    message_count: 1, is_starred: false, has_attachments: false, account_ids: ['a1'],
  };
}

function rawThread(id: string, latestMessageAt = 1): ThreadListItemResponse {
  return {
    id,
    latest_message_id: `message-${id}`,
    latest_message_at: latestMessageAt,
    subject: `Subject ${id}`,
    participants_summary: 'Sender <sender@example.com>',
    latest_snippet: 'preview',
    message_count: 1,
    unread_count: 0,
    is_starred: false,
    has_attachments: false,
    account_count: 1,
    account_ids: ['a1'],
    pending_operation_count: 0,
    projection_version: 1,
  };
}

test('thread query key is stable and user mailbox filter cursor scoped', () => {
  assert.equal(
    createThreadQueryKey('u1', { scope: 'semantic', key: 'inbox', filters: { unread: true, account_ids: ['a2', 'a1'] }, cursor: 'c1' }),
    createThreadQueryKey('u1', { cursor: 'c1', filters: { account_ids: ['a2', 'a1'], unread: true }, key: 'inbox', scope: 'semantic' }),
  );
});

test('next cursor appends without duplicates and precise patch preserves other rows', () => {
  const first = [thread('t1', true), thread('t2')];
  const merged = appendThreadPage(first, [thread('t2'), thread('t3')]);
  assert.deepEqual(merged.map((item) => item.id), ['t1', 't2', 't3']);
  const patched = patchThreadProjection(merged, 't1', { unread_count: 0, is_starred: true });
  assert.equal(patched[0].unread_count, 0);
  assert.equal(patched[1], merged[1]);
  assert.equal(patched[2], merged[2]);
});

test('backend items response is normalized before empty or populated pages are cached', () => {
  const memory = new ThreadCursorMemory();
  const empty = memory.set('empty', { items: [], next_cursor: null });
  assert.deepEqual(empty.threads, []);

  const populated = memory.set('inbox', {
    items: [{
      ...rawThread('t1', 123),
      message_count: 2,
      unread_count: 1,
      is_starred: true,
      pending_operation_count: 1,
      projection_version: 3,
    }],
    next_cursor: 'next',
  });
  assert.equal(populated.threads[0].latest_at, 123);
  assert.equal(populated.threads[0].snippet, 'preview');
  assert.equal(populated.threads[0].pending_state, 'pending');
  assert.equal(populated.next_cursor, 'next');
});

test('switching mailbox aborts old request and stale response cannot replace new data', async () => {
  const events: string[] = [];
  const controller = new ThreadListController(async (request, signal) => {
    events.push(`start:${request.key}`);
    return new Promise((resolve, reject) => {
      signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      setTimeout(() => resolve({ items: [rawThread(request.key)], next_cursor: null }), request.key === 'old' ? 20 : 1);
    });
  });
  const oldRequest = assert.rejects(controller.load({ key: 'old' }), { name: 'AbortError' });
  const next = await controller.load({ key: 'new' });
  await oldRequest;
  assert.equal(next.items[0].id, 'new');
  assert.equal(controller.current?.items[0].id, 'new');
  assert.deepEqual(events, ['start:old', 'start:new']);
});

test('selection keyboard and mobile mode move focus predictably', () => {
  const selection = createThreadSelection(['t1', 't2', 't3']);
  assert.equal(shouldHandleListShortcut({ tagName: 'INPUT' }, 'ArrowDown'), false);
  assert.equal(shouldHandleListShortcut({ tagName: 'DIV' }, 'ArrowDown'), true);
  selection.move(1);
  selection.move(1);
  assert.equal(selection.focusedId, 't3');
  selection.enterMobileSelection('t2');
  assert.equal(selection.mode, 'selecting');
  assert.deepEqual(selection.selectedIds, ['t2']);
  selection.remove('t2');
  assert.equal(selection.focusedId, 't3');
});

test('thread row exposes complete screen-reader status and stable key rendering', async () => {
  const row = await readFile(new URL('../../src/features/threads/ThreadRow.vue', import.meta.url), 'utf8');
  const list = await readFile(new URL('../../src/features/threads/ThreadList.vue', import.meta.url), 'utf8');
  assert.match(row, /未读/);
  assert.match(row, /附件/);
  assert.match(row, /待同步/);
  assert.match(list, /:key="thread.id"/);
  assert.doesNotMatch(list, /v-html/);
});

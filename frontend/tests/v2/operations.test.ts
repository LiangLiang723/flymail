import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import {
  OperationCommandAdapter,
  canPermanentlyDelete,
  conflictResolutions,
  undoRemainingMs,
} from '../../src/features/operations/operation-actions.ts';

test('command deduplicates in flight and applies only returned server projection', async () => {
  let calls = 0;
  const patches: unknown[] = [];
  let release: ((value: unknown) => void) | undefined;
  const adapter = new OperationCommandAdapter({
    submit: async () => {
      calls += 1;
      return new Promise((resolve) => { release = resolve; });
    },
    patchProjection: (_id, projection) => patches.push(projection),
    fetchAuthoritativeProjection: async () => ({ id: 't1', unread_count: 1 }),
  });
  const first = adapter.execute({ target_type: 'thread', target_id: 't1', operation_type: 'read', desired_state: { read: true } });
  const second = adapter.execute({ target_type: 'thread', target_id: 't1', operation_type: 'read', desired_state: { read: true } });
  assert.equal(first, second);
  release?.({ operation_group_id: 'g1', operation_ids: ['o1'], projection: { id: 't1', unread_count: 0 } });
  await first;
  assert.equal(calls, 1);
  assert.deepEqual(patches, [{ id: 't1', unread_count: 0 }]);
});

test('command failure restores authoritative server projection', async () => {
  const patches: unknown[] = [];
  const adapter = new OperationCommandAdapter({
    submit: async () => { throw new Error('provider failed'); },
    patchProjection: (_id, projection) => patches.push(projection),
    fetchAuthoritativeProjection: async () => ({ id: 't1', is_starred: false }),
  });
  await assert.rejects(adapter.execute({ target_type: 'thread', target_id: 't1', operation_type: 'star', desired_state: { starred: true } }));
  assert.deepEqual(patches, [{ id: 't1', is_starred: false }]);
});

test('undo uses server expiry and permanent delete requires exact typed target', () => {
  assert.equal(undoRemainingMs(2_000, 1_500), 500);
  assert.equal(undoRemainingMs(1_000, 1_500), 0);
  assert.equal(canPermanentlyDelete('Project Alpha', 'Project Alpha'), true);
  assert.equal(canPermanentlyDelete('Project Alpha', 'project alpha'), false);
});

test('conflict center exposes only API supported resolutions', () => {
  assert.deepEqual(conflictResolutions('draft_version'), ['keep_local', 'keep_remote', 'save_copy']);
  assert.deepEqual(conflictResolutions('uncertain_send'), ['mark_sent', 'retry', 'cancel']);
  assert.deepEqual(conflictResolutions('unknown'), []);
});

test('operation UI exposes pending, partial details, keyboard undo and mobile toolbar', async () => {
  const actions = await readFile(new URL('../../src/features/operations/ThreadActions.vue', import.meta.url), 'utf8');
  const undo = await readFile(new URL('../../src/features/operations/UndoToast.vue', import.meta.url), 'utf8');
  const pending = await readFile(new URL('../../src/features/operations/PendingState.vue', import.meta.url), 'utf8');
  const conflicts = await readFile(new URL('../../src/features/operations/ConflictCenter.vue', import.meta.url), 'utf8');
  assert.match(actions, /v2-mobile-operation-toolbar/);
  assert.match(actions, /mark-all-read/);
  assert.match(undo, /到期时间|expiresAt/);
  assert.match(pending, /partialResults/);
  assert.match(conflicts, /conflictResolutions/);
  assert.doesNotMatch(`${actions}${conflicts}`, /v-html/);
});

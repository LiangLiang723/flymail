import test from 'node:test';
import assert from 'node:assert/strict';

import { applyRealtimeEvent, decodeRealtimeEvent } from '../../src/shared/realtime/events.ts';
import { RealtimeClient, reconnectDelay } from '../../src/shared/realtime/client.ts';

function event(sequence: number, eventType = 'thread.updated', payload: Record<string, unknown> = {}) {
  return { sequence, event_type: eventType, aggregate_id: 'id-1', occurred_at: 1, payload };
}

test('decoder accepts known schemas and ignores unknown or malformed events', () => {
  assert.equal(decodeRealtimeEvent(event(1))?.event_type, 'thread.updated');
  assert.equal(decodeRealtimeEvent(event(0)), undefined);
  assert.equal(decodeRealtimeEvent(event(1, 'unknown.event')), undefined);
});

test('event application patches exact scopes only', () => {
  const actions: string[] = [];
  const handlers = {
    patchThread: (id: string) => actions.push(`thread:${id}`),
    invalidateThread: (id: string) => actions.push(`detail:${id}`),
    invalidateBody: (id: string) => actions.push(`body:${id}`),
    invalidateScopes: (scopes: string[]) => actions.push(`scopes:${scopes.join(',')}`),
    authExpired: () => actions.push('auth'),
    versionChanged: (version: string) => actions.push(`version:${version}`),
  };
  applyRealtimeEvent(event(1, 'thread.updated', { thread_id: 't1', projection: { unread_count: 0 } }), handlers);
  applyRealtimeEvent(event(2, 'message.body_state', { message_id: 'm1', state: 'ready' }), handlers);
  applyRealtimeEvent(event(3, 'system.version_changed', { version: '0.0.26' }), handlers);
  assert.deepEqual(actions, ['thread:t1', 'detail:t1', 'body:m1', 'version:0.0.26']);
});

test('monotonic events apply once and a gap requests backlog', async () => {
  const applied: number[] = [];
  const backlogCalls: number[] = [];
  const client = new RealtimeClient({
    initialSequence: 1,
    fetchBacklog: async (after) => {
      backlogCalls.push(after);
      return { events: [event(2)], current_sequence: 2 };
    },
    handlers: { onEvent: (value) => applied.push(value.sequence) },
  });
  await client.handleEnvelope({ type: 'events', events: [event(3), event(3)], current_sequence: 3 });
  assert.deepEqual(backlogCalls, [1]);
  assert.deepEqual(applied, [2, 3]);
  assert.equal(client.sequence, 3);
});

test('resync, auth and heartbeat states are explicit', async () => {
  const actions: string[] = [];
  const client = new RealtimeClient({ handlers: {
    invalidateScopes: (scopes) => actions.push(scopes.join(',')),
    authExpired: () => actions.push('auth'),
  } });
  await client.handleEnvelope({ type: 'ping', current_sequence: 4 });
  assert.equal(client.state, 'online');
  await client.handleEnvelope({ type: 'resync_required', details: { scopes: ['threads', 'sync'] } });
  assert.equal(client.state, 'resync_required');
  client.handleClose(4401);
  assert.deepEqual(actions, ['threads,sync', 'auth']);
});

test('reconnect is bounded, resets when stable and invokes status fallback', () => {
  assert.equal(reconnectDelay(0, () => .5), 1000);
  assert.equal(reconnectDelay(20, () => .5), 30000);
  let fallback = 0;
  const client = new RealtimeClient({ handlers: { statusFallback: () => { fallback += 1; } } });
  client.recordReconnectAttempt();
  client.recordReconnectAttempt();
  assert.equal(client.reconnectAttempts, 2);
  client.markStable();
  assert.equal(client.reconnectAttempts, 0);
  client.handleClose(1006);
  assert.equal(client.state, 'reconnecting');
  assert.equal(fallback, 1);
});

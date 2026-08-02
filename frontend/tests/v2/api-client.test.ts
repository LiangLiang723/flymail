import test from 'node:test';
import assert from 'node:assert/strict';

import { ApiClient, type TransportRequest } from '../../src/shared/api/client.ts';
import { ApiError, normalizeApiError } from '../../src/shared/api/errors.ts';
import { QueryCache } from '../../src/shared/api/query-cache.ts';

test('api client applies credentials and csrf centrally', async () => {
  let captured: TransportRequest | undefined;
  const client = new ApiClient({
    csrfToken: () => 'csrf-token',
    transport: async (request) => {
      captured = request;
      return { status: 200, data: { ok: true }, headers: { 'x-request-id': 'req-1' } };
    },
  });

  const result = await client.request<{ ok: boolean }>({ method: 'POST', path: '/api/v2/settings', body: {} });
  assert.deepEqual(result, { ok: true });
  assert.equal(captured?.withCredentials, true);
  assert.equal(captured?.headers['X-CSRF-Token'], 'csrf-token');
});

test('401 clears user cache and emits auth expired once', async () => {
  const cache = new QueryCache('user-a');
  cache.set(['threads'], [{ id: 'thread-1' }], 60_000);
  let expired = 0;
  const client = new ApiClient({
    cache,
    csrfToken: () => 'csrf-token',
    transport: async () => ({
      status: 401,
      data: { error: { code: 'authentication_required', message: '请先登录', request_id: 'req-auth' } },
      headers: {},
    }),
  });
  client.onAuthExpired(() => { expired += 1; });

  await assert.rejects(
    client.request({ method: 'GET', path: '/api/v2/bootstrap' }),
    (error: unknown) => error instanceof ApiError && error.status === 401,
  );
  assert.equal(cache.get(['threads']), undefined);
  assert.equal(expired, 1);
});

test('same cache key shares one in-flight promise and stale data refreshes in background', async () => {
  const cache = new QueryCache('user-a', () => 1_000);
  let calls = 0;
  let release: ((value: string[]) => void) | undefined;
  const fetcher = () => {
    calls += 1;
    return new Promise<string[]>((resolve) => { release = resolve; });
  };

  const first = cache.fetch(['threads', 'inbox'], fetcher, { staleMs: 100 });
  const second = cache.fetch(['threads', 'inbox'], fetcher, { staleMs: 100 });
  assert.equal(first, second);
  release?.(['thread-1']);
  assert.deepEqual(await first, ['thread-1']);
  assert.equal(calls, 1);

  cache.set(['threads', 'inbox'], ['cached'], 0);
  const stale = await cache.fetch(['threads', 'inbox'], async () => {
    calls += 1;
    return ['fresh'];
  }, { staleMs: 100, staleWhileRevalidate: true });
  assert.deepEqual(stale, ['cached']);
  await cache.waitForIdle(['threads', 'inbox']);
  assert.deepEqual(cache.get(['threads', 'inbox']), ['fresh']);
});

test('cache cancellation aborts request and patch preserves unrelated rows', async () => {
  const cache = new QueryCache('user-a');
  cache.set(['threads'], [
    { id: 'thread-1', unread: true },
    { id: 'thread-2', unread: false },
  ], 60_000);
  const before = cache.get<Array<{ id: string; unread: boolean }>>(['threads'])!;
  cache.patch(['threads'], (rows) => rows.map((row) => row.id === 'thread-1' ? { ...row, unread: false } : row));
  const after = cache.get<Array<{ id: string; unread: boolean }>>(['threads'])!;
  assert.notEqual(after[0], before[0]);
  assert.equal(after[1], before[1]);

  let aborted = false;
  const pending = cache.fetch(['slow'], (_signal) => new Promise<string>((_resolve, reject) => {
    _signal.addEventListener('abort', () => {
      aborted = true;
      reject(new DOMException('aborted', 'AbortError'));
    });
  }));
  cache.cancel(['slow']);
  await assert.rejects(pending, { name: 'AbortError' });
  assert.equal(aborted, true);
});

test('backend error envelope maps to typed ApiError', () => {
  const error = normalizeApiError({
    status: 409,
    data: {
      error: {
        code: 'conflict',
        message: '版本冲突',
        request_id: 'req-conflict',
        details: { current_version: 3 },
      },
    },
  });
  assert.ok(error instanceof ApiError);
  assert.equal(error.code, 'conflict');
  assert.equal(error.requestId, 'req-conflict');
  assert.deepEqual(error.details, { current_version: 3 });
});

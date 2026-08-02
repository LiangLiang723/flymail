import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import {
  SearchController,
  appendSearchResults,
  deserializeSearchFilters,
  serializeSearchFilters,
  validateSearchFilters,
} from '../../src/features/search/search-state.ts';

test('structured filters round-trip through route without raw syntax', () => {
  const filters = validateSearchFilters({ keyword: 'alpha', from_addresses: ['a@example.com'], is_read: false, has_attachment: true });
  assert.deepEqual(deserializeSearchFilters(serializeSearchFilters(filters)), filters);
});

test('typing debounce aborts prior request and only latest result wins', async () => {
  const calls: string[] = [];
  const controller = new SearchController(async (filters, signal) => {
    calls.push(filters.keyword || '');
    return new Promise((resolve, reject) => {
      signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      setTimeout(() => resolve({ items: [{ thread_id: filters.keyword || '', matched_message_id: 'm', matched_field: 'subject', subject: '', snippet: '', received_at: 1, account_ids: [], unread: false, starred: false, has_attachment: false }], next_cursor: null, fulltext_parser: 'ngram' }), 1);
    });
  }, 0);
  const first = controller.search({ keyword: 'a' });
  const second = controller.search({ keyword: 'ab' });
  await assert.rejects(first, { name: 'AbortError' });
  assert.equal((await second).items[0].thread_id, 'ab');
  assert.deepEqual(calls, ['a', 'ab']);
});

test('result pages aggregate by thread and preserve newest matching message', () => {
  const current = [{ thread_id: 't1', matched_message_id: 'm1', matched_field: 'subject', subject: 'A', snippet: 'one', received_at: 1, account_ids: [], unread: false, starred: false, has_attachment: false }];
  const next = [
    { ...current[0], matched_message_id: 'm2', received_at: 2, snippet: 'two' },
    { ...current[0], thread_id: 't2', matched_message_id: 'm3' },
  ];
  const merged = appendSearchResults(current, next);
  assert.deepEqual(merged.map((item) => item.thread_id), ['t1', 't2']);
  assert.equal(merged[0].matched_message_id, 'm2');
});

test('invalid saved filters are rejected before restore', () => {
  assert.throws(() => validateSearchFilters({ date_from: 20, date_to: 10 }));
  assert.throws(() => validateSearchFilters({ min_size_bytes: 20, max_size_bytes: 10 }));
});

test('search UI exposes cached-body boundary, accessible suggestions and mobile apply/cancel sheet', async () => {
  const page = await readFile(new URL('../../src/features/search/SearchPage.vue', import.meta.url), 'utf8');
  const bar = await readFile(new URL('../../src/features/search/SearchBar.vue', import.meta.url), 'utf8');
  const filters = await readFile(new URL('../../src/features/search/AdvancedFilters.vue', import.meta.url), 'utf8');
  const results = await readFile(new URL('../../src/features/search/SearchResults.vue', import.meta.url), 'utf8');
  assert.match(page, /已缓存正文|cached/i);
  assert.match(page, /search\/history/);
  assert.match(page, /saved-searches/);
  assert.match(bar, /role="listbox"/);
  assert.match(bar, /ArrowDown/);
  assert.match(filters, /role="dialog"/);
  assert.match(filters, /应用/);
  assert.match(filters, /取消/);
  assert.match(results, /matched_field/);
  assert.match(results, /正文缓存已释放|metadata/);
  assert.doesNotMatch(`${page}${bar}${filters}${results}`, /v-html/);
});

import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createEmptyMailSearch,
  hasMailSearchFilters,
  serializeMailSearchParams,
} from '../src/utils/mail-search.ts';

test('mail search serializes only active structured filters', () => {
  const state = {
    ...createEmptyMailSearch(),
    keyword: '季度 报告',
    fromAddr: 'alice@example.com',
    toAddr: 'bob@example.com',
    subject: '项目',
    body: '里程碑',
    after: '2026-07-01',
    before: '2026-08-01',
    readFilter: 'unread' as const,
    attachmentOnly: true,
    starredOnly: true,
  };

  assert.deepEqual(serializeMailSearchParams(state), {
    keyword: '季度 报告',
    from_addr: 'alice@example.com',
    to_addr: 'bob@example.com',
    subject: '项目',
    body: '里程碑',
    after: '2026-07-01',
    before: '2026-08-01',
    read_filter: 'unread',
    attachment_filter: true,
    starred_filter: true,
  });
  assert.equal(hasMailSearchFilters(state), true);
});

test('empty mail search omits filters and is reusable for clear action', () => {
  const state = createEmptyMailSearch();
  assert.deepEqual(serializeMailSearchParams(state), {});
  assert.equal(hasMailSearchFilters(state), false);
  assert.deepEqual(state, {
    keyword: '',
    fromAddr: '',
    toAddr: '',
    subject: '',
    body: '',
    after: '',
    before: '',
    readFilter: '',
    attachmentOnly: false,
    starredOnly: false,
  });
});

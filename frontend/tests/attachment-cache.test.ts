import test from 'node:test';
import assert from 'node:assert/strict';
import { formatStorageBytes, isValidAttachmentCacheLimit } from '../src/utils/attachment-cache.ts';

test('validates zero or values of at least 100 MB', () => {
  assert.equal(isValidAttachmentCacheLimit(0), true);
  assert.equal(isValidAttachmentCacheLimit(99), false);
  assert.equal(isValidAttachmentCacheLimit(100), true);
  assert.equal(isValidAttachmentCacheLimit(2048), true);
});

test('formats byte values for storage display', () => {
  assert.equal(formatStorageBytes(0), '0 B');
  assert.equal(formatStorageBytes(1024), '1 KB');
  assert.equal(formatStorageBytes(1024 * 1024), '1 MB');
  assert.equal(formatStorageBytes(1.5 * 1024 * 1024 * 1024), '1.5 GB');
});

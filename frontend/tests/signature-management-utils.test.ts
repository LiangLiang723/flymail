import test from 'node:test';
import assert from 'node:assert/strict';
import {
  duplicateSignatureDraft,
  filterSignatures,
  resolveDefaultSignature,
  serializeSignatureDraft,
} from '../src/utils/signature-management.ts';

const signatures = [
  { id: 1, name: '全局', content_html: '<p>global</p>', account_id: '', is_default: true, is_reply_default: true },
  { id: 2, name: '工作', content_html: '<p>work</p>', account_id: 'account-1', is_default: true, is_reply_default: false },
];

test('account default overrides global default', () => {
  assert.equal(resolveDefaultSignature(signatures, 'account-1', 'new')?.id, 2);
  assert.equal(resolveDefaultSignature(signatures, 'account-2', 'new')?.id, 1);
  assert.equal(resolveDefaultSignature(signatures, 'account-1', 'draft'), null);
});

test('duplicate clears both default flags', () => {
  const draft = duplicateSignatureDraft(signatures[1]);
  assert.equal(draft.name, '工作 - 副本');
  assert.equal(draft.is_default, false);
  assert.equal(draft.is_reply_default, false);
  assert.equal(draft.id, null);
});

test('filter matches name and account scope', () => {
  assert.deepEqual(filterSignatures(signatures, '工作', 'account-1').map((item) => item.id), [2]);
  assert.deepEqual(filterSignatures(signatures, '', '').map((item) => item.id), [1]);
  assert.deepEqual(filterSignatures(signatures, '', 'all').map((item) => item.id), [1, 2]);
});

test('serialized draft changes when one form field changes', () => {
  const first = duplicateSignatureDraft(signatures[0]);
  const second = { ...first, content_html: '<p>changed</p>' };
  assert.notEqual(serializeSignatureDraft(first), serializeSignatureDraft(second));
});

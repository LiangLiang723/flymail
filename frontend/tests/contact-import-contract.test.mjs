import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const readSource = (relativePath) => readFile(path.join(frontendRoot, relativePath), 'utf8');

test('contacts page imports selected candidates from one mailbox', async () => {
  const source = await readSource('src/views/ContactList.vue');
  assert.match(source, /从邮件导入/);
  assert.match(source, /选择邮箱/);
  assert.match(source, /候选联系人/);
  assert.match(source, /selectedCandidateEmails/);
  assert.match(source, /loadContactCandidates/);
  assert.match(source, /importContactCandidates/);
});

test('contacts composable exposes preview and bulk import APIs', async () => {
  const source = await readSource('src/composables/useContacts.ts');
  assert.match(source, /export interface ContactCandidate/);
  assert.match(source, /\/contacts\/candidates/);
  assert.match(source, /\/contacts\/import/);
  assert.match(source, /async function loadContactCandidates/);
  assert.match(source, /async function importContactCandidates/);
});

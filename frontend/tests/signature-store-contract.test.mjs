import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const readSource = (relativePath) => readFile(path.join(frontendRoot, relativePath), 'utf8');

test('signature store owns loading, editing and CRUD state', async () => {
  const source = await readSource('src/stores/signatures.ts');

  assert.match(source, /defineStore\('signatures'/);
  assert.match(source, /api\.get\('\/signatures'\)/);
  assert.match(source, /api\.post\('\/signatures'/);
  assert.match(source, /api\.put\(`\/signatures\/\$\{draft\.value\.id\}`/);
  assert.match(source, /api\.delete\(`\/signatures\/\$\{selectedId\.value\}`/);
  assert.match(source, /const hasUnsavedChanges = computed/);
  assert.match(source, /duplicateSignatureDraft/);
  assert.match(source, /duplicate\.is_default\s*=\s*false/);
  assert.match(source, /duplicate\.is_reply_default\s*=\s*false/);
});

test('signature store exposes the complete management workspace', async () => {
  const source = await readSource('src/stores/signatures.ts');

  for (const name of [
    'signatures', 'loaded', 'loading', 'saving', 'deleting', 'search', 'accountFilter',
    'selectedId', 'draft', 'savedDraftSnapshot', 'entrySource', 'mobileEditing',
    'filteredSignatures', 'selectedSignature', 'hasUnsavedChanges', 'signatureCount',
    'loadSignatures', 'ensureLoaded', 'beginCreate', 'beginEdit', 'beginDuplicate',
    'saveDraft', 'deleteSelected', 'discardDraft', 'setEntrySource', 'resetWorkspace',
  ]) {
    assert.match(source, new RegExp(`\\b${name}\\b`));
  }
});

import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const readSource = (relativePath) => readFile(path.join(frontendRoot, relativePath), 'utf8');

test('applied search filters stay inside a fixed-height toolbar control', async () => {
  const searchSource = await readSource('src/components/mail/MailSearchBar.vue');

  assert.doesNotMatch(searchSource, /class="mail-search-summary"/);
  assert.doesNotMatch(searchSource, /\.mail-search-summary\s*\{/);
  assert.match(searchSource, /filter-count/);
  assert.match(searchSource, /:aria-label="activeLabels\.length[^\n]*已应用[^\n]*个条件/);
  assert.match(searchSource, /:aria-expanded="advancedOpen"/);
  assert.match(searchSource, /\.mail-search-bar\s*\{[^}]*min-width:\s*0;/s);
  assert.match(searchSource, /\.mail-search-input-wrap\s*\{[^}]*min-width:\s*0;/s);
  assert.match(
    searchSource,
    /\.mail-search-action,\s*\.mail-search-primary,\s*\.mail-search-clear\s*\{[^}]*flex-shrink:\s*0;/s,
  );
  assert.match(
    searchSource,
    /@media \(max-width:\s*900px\)[\s\S]*\.mail-search-main > \.mail-search-primary\s*\{[^}]*display:\s*none;/s,
  );
});

test('desktop mail toolbar never relies on implicit wrapping for search controls', async () => {
  const mailListSource = await readSource('src/views/MailList.vue');

  assert.match(
    mailListSource,
    /\.toolbar-right\s*\{[^}]*flex-wrap:\s*nowrap;[^}]*justify-content:\s*flex-end;/s,
  );
  assert.match(
    mailListSource,
    /@media \(max-width:\s*768px\)[\s\S]*\.list-toolbar\s*\{[^}]*flex-wrap:\s*wrap;/s,
  );
  assert.match(
    mailListSource,
    /@media \(max-width:\s*768px\)[\s\S]*\.toolbar-right\s*\{[^}]*width:\s*100%;[^}]*flex-wrap:\s*nowrap;/s,
  );
});

import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const readSource = (relativePath) => readFile(path.join(frontendRoot, relativePath), 'utf8');

test('recipient chips keep address text bounded and use a compact remove control', async () => {
  const compose = await readSource('src/views/ComposeEmail.vue');
  const pageSystem = await readSource('src/styles/page-system.css');

  assert.ok(
    (compose.match(/class="tag-label"\s+:title="addr"/g) || []).length >= 3,
    'to/cc/bcc chips should each wrap the address in a truncatable label',
  );
  assert.match(
    pageSystem,
    /\.compose-page \.tag\s*\{[^}]*min-width:\s*0;[^}]*max-width:/s,
  );
  assert.match(
    pageSystem,
    /\.compose-page \.tag-label\s*\{[^}]*min-width:\s*0;[^}]*overflow:\s*hidden;[^}]*text-overflow:\s*ellipsis;[^}]*white-space:\s*nowrap;/s,
  );
  assert.match(
    pageSystem,
    /\.compose-page \.tag-remove\s*\{[^}]*width:\s*20px;[^}]*height:\s*20px;/s,
  );
});

test('contact suggestions bound both long names and addresses inside the popup', async () => {
  const compose = await readSource('src/views/ComposeEmail.vue');

  assert.match(
    compose,
    /\.contact-suggestions strong,\s*\.contact-suggestions small\s*\{[^}]*min-width:\s*0;[^}]*overflow:\s*hidden;[^}]*text-overflow:\s*ellipsis;[^}]*white-space:\s*nowrap;/s,
  );
});

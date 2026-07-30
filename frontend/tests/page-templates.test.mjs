import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (file) => readFile(path.join(root, file), 'utf8');

test('the authenticated shell owns one complete viewport height chain', async () => {
  const base = await read('src/styles/base.css');
  const shell = await read('src/styles/app-shell.css');
  const app = await read('src/App.vue');

  assert.match(base, /html[\s\S]*body[\s\S]*#app[\s\S]*height:\s*100%/);
  assert.match(base, /body\s*\{[^}]*overflow:\s*hidden/s);
  assert.match(shell, /\.app-shell\s*\{[^}]*height:\s*100dvh/s);
  assert.match(shell, /\.main[\s\S]*\.content[\s\S]*height:\s*100%/);
  assert.match(app, /class="app-page-viewport"/);
});

test('the page viewport clips outer overflow and delegates scrolling to templates', async () => {
  const shell = await read('src/styles/app-shell.css');

  assert.match(shell, /\.app-page-viewport\s*\{[^}]*min-height:\s*0/s);
  assert.match(shell, /\.app-page-viewport\s*\{[^}]*overflow:\s*hidden/s);
});

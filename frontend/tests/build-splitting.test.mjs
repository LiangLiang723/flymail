import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (file) => readFile(path.join(root, file), 'utf8');

const authenticatedViews = [
  'About',
  'AccountList',
  'Backup',
  'ComposeEmail',
  'ContactList',
  'HistorySync',
  'MailList',
  'NotificationSettings',
  'Profile',
  'Settings',
  'UnifiedInbox',
  'UserManagement',
];

test('the frontend build toolchain uses the patched Vite 6 line', async () => {
  const pkg = JSON.parse(await read('package.json'));

  assert.equal(pkg.devDependencies.vite, '^6.4.3');
  assert.equal(pkg.devDependencies['@vitejs/plugin-vue'], '^5.2.4');
  assert.deepEqual(pkg.allowScripts, {
    'esbuild@0.25.12': true,
    'vue-demi@0.14.10': true,
  });
});

test('the frontend lockfile is committed and Docker installs it reproducibly', async () => {
  const gitignore = await read('../.gitignore');
  const dockerfile = await read('../Dockerfile');

  assert.match(gitignore, /^!frontend\/package-lock\.json$/m);
  assert.match(dockerfile, /COPY frontend\/package\*\.json \.\/\nRUN npm ci/);
});

test('authenticated top-level pages are loaded asynchronously', async () => {
  const source = await read('src/App.vue');

  assert.match(source, /import \{[^}]*defineAsyncComponent[^}]*\} from 'vue';/);
  assert.match(source, /import LoginView from '\.\/views\/LoginView\.vue';/);

  for (const view of authenticatedViews) {
    assert.match(
      source,
      new RegExp(`const ${view} = defineAsyncComponent\\(\\(\\) => import\\('\\./views/${view}\\.vue'\\)\\);`),
      `${view} should be loaded with defineAsyncComponent`,
    );
    assert.doesNotMatch(
      source,
      new RegExp(`import ${view} from '\\./views/${view}\\.vue';`),
      `${view} should not be imported eagerly`,
    );
  }
});

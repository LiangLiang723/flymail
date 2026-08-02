import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';


test('normal frontend entry and production build target are V2', async () => {
  const main = await readFile(new URL('../../src/main.ts', import.meta.url), 'utf8');
  const config = await readFile(new URL('../../vite.config.ts', import.meta.url), 'utf8');
  const worker = await readFile(new URL('../../public/flymail-sw.js', import.meta.url), 'utf8');
  const manifest = await readFile(new URL('../../public/manifest.webmanifest', import.meta.url), 'utf8');
  const packageJson = JSON.parse(await readFile(new URL('../../package.json', import.meta.url), 'utf8'));

  assert.match(main, /AppV2/);
  assert.match(main, /createV2Router/);
  assert.doesNotMatch(main, /\.\/App\.vue/);
  assert.doesNotMatch(config, /FLYMAIL_V2_BUILD/);
  assert.match(config, /input:\s*resolve\(__dirname, 'index\.html'\)/);
  assert.match(config, /outDir:\s*'\.\.\/dist\/ui'/);
  assert.match(config, /manifest:\s*true/);
  assert.match(worker, /const STATIC_FALLBACK = '\/'/);
  assert.doesNotMatch(worker, /v2\.html/);
  assert.match(manifest, /"start_url":\s*"\/"/);
  assert.equal(packageJson.scripts.build, 'vue-tsc && vite build && node scripts/check-v2-bundle-budget.mjs ../dist/ui');
});

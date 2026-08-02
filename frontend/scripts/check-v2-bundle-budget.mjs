import { readFile, readdir } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { gzipSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';

const DEFAULT_INITIAL_LIMIT = 180 * 1024;
const DEFAULT_ASYNC_LIMIT = 120 * 1024;

async function gzipBytes(path) {
  return gzipSync(await readFile(path), { level: 9 }).byteLength;
}

function collectInitialKeys(manifest) {
  const initial = new Set();
  const visit = (key) => {
    if (!key || initial.has(key) || !manifest[key]) return;
    initial.add(key);
    for (const imported of manifest[key].imports || []) visit(imported);
  };
  for (const [key, value] of Object.entries(manifest)) {
    if (value?.isEntry) visit(key);
  }
  return initial;
}

export async function measureBundleBudget(
  buildRoot,
  {
    initialLimitBytes = DEFAULT_INITIAL_LIMIT,
    asyncLimitBytes = DEFAULT_ASYNC_LIMIT,
  } = {},
) {
  const manifestPath = join(buildRoot, '.vite', 'manifest.json');
  const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
  const initialKeys = collectInitialKeys(manifest);
  const initialFiles = [];
  const initialPaths = new Set();

  for (const key of initialKeys) {
    const file = manifest[key]?.file;
    if (!file?.endsWith('.js') || initialPaths.has(file)) continue;
    initialPaths.add(file);
    initialFiles.push({ file, gzipBytes: await gzipBytes(join(buildRoot, file)) });
  }

  const assetFiles = await readdir(join(buildRoot, 'assets'));
  const asyncFiles = [];
  for (const name of assetFiles.filter((value) => value.endsWith('.js')).sort()) {
    const file = `assets/${name}`;
    if (initialPaths.has(file)) continue;
    asyncFiles.push({ file, gzipBytes: await gzipBytes(join(buildRoot, file)) });
  }

  const initialGzipBytes = initialFiles.reduce((total, item) => total + item.gzipBytes, 0);
  const oversizedAsync = asyncFiles.filter((item) => item.gzipBytes > asyncLimitBytes);
  return {
    initialLimitBytes,
    asyncLimitBytes,
    initialGzipBytes,
    initialFiles,
    asyncFiles,
    oversizedAsync,
    withinBudget: initialGzipBytes <= initialLimitBytes && oversizedAsync.length === 0,
  };
}

function kib(value) {
  return `${(value / 1024).toFixed(2)} KiB`;
}

async function main() {
  const scriptDir = dirname(fileURLToPath(import.meta.url));
  const buildRoot = process.argv[2] || join(scriptDir, '..', '..', 'dist', 'v2-ui');
  const result = await measureBundleBudget(buildRoot);
  console.log(`V2 initial JS gzip: ${kib(result.initialGzipBytes)} / ${kib(result.initialLimitBytes)}`);
  for (const item of result.asyncFiles) {
    console.log(`V2 async JS gzip: ${item.file} ${kib(item.gzipBytes)} / ${kib(result.asyncLimitBytes)}`);
  }
  if (!result.withinBudget) {
    if (result.initialGzipBytes > result.initialLimitBytes) {
      console.error('V2 initial bundle exceeds the 180 KiB gzip budget.');
    }
    for (const item of result.oversizedAsync) {
      console.error(`${item.file} exceeds the 120 KiB gzip async chunk budget.`);
    }
    process.exitCode = 1;
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  await main();
}

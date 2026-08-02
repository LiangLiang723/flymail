import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import process from 'node:process';

const root = resolve(import.meta.dirname, '..');
const fixturePath = resolve(root, '../backend/tests/v2/fixtures/openapi-v2.json');
const generatedPath = resolve(root, 'src/shared/api/generated.ts');
const fixture = JSON.parse(await readFile(fixturePath, 'utf8'));
const current = await readFile(generatedPath, 'utf8');
const expected = current
  .replace(/export const OPENAPI_VERSION = '[^']+' as const;/, `export const OPENAPI_VERSION = '${fixture.version}' as const;`)
  .replace(/export const OPENAPI_SHA256 = '[a-f0-9]+' as const;/, `export const OPENAPI_SHA256 = '${fixture.sha256}' as const;`);

if (process.argv.includes('--check')) {
  if (expected !== current) {
    console.error('V2 API type fingerprint is stale. Run npm run generate:v2-api.');
    process.exit(1);
  }
  console.log(`V2 API type fingerprint is current (${fixture.version}, ${fixture.sha256}).`);
} else {
  await writeFile(generatedPath, expected, 'utf8');
  console.log(`Updated V2 API type fingerprint to ${fixture.version}.`);
}

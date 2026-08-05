import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

async function readSource(relativePath) {
  return readFile(path.join(frontendRoot, relativePath), 'utf8');
}

test('shared switch primitive owns switch semantics and model updates', async () => {
  const source = await readSource('src/components/ui/UiSwitch.vue');

  assert.match(source, /role="switch"/);
  assert.match(source, /update:modelValue/);
  assert.match(source, /ui-switch__input/);
  assert.match(source, /ui-switch__track/);
  assert.match(source, /ui-switch__knob/);
  assert.match(source, /:aria-label="label"/);
});

test('shared checkbox primitive owns boolean and collection model updates', async () => {
  const source = await readSource('src/components/ui/UiCheckbox.vue');

  assert.match(source, /update:modelValue/);
  assert.match(source, /Array\.isArray\(props\.modelValue\)/);
  assert.match(source, /ui-checkbox__input/);
  assert.match(source, /ui-checkbox__box/);
  assert.match(source, /:aria-label="label"/);
});

test('account-selection checkboxes use the shared checkbox primitive', async () => {
  const unified = await readSource('src/views/UnifiedInbox.vue');

  assert.equal((unified.match(/<UiCheckbox\b/g) || []).length, 1);
  assert.doesNotMatch(unified, /<input[^>]+type="checkbox"/s);
});

test('all slider-style boolean controls use UiSwitch instead of page-specific implementations', async () => {
  const account = await readSource('src/views/AccountList.vue');
  const settings = await readSource('src/views/Settings.vue');
  const notifications = await readSource('src/views/NotificationSettings.vue');

  assert.equal((account.match(/<UiSwitch\b/g) || []).length, 2);
  assert.doesNotMatch(account, /class="toggle-switch"/);

  assert.ok((settings.match(/<UiSwitch\b/g) || []).length >= 2);
  assert.doesNotMatch(settings, /settings-toggle-(?:control|input|switch|knob)/);
  assert.doesNotMatch(settings, /<input[^>]+type="checkbox"/s);

  assert.equal((notifications.match(/<UiSwitch\b/g) || []).length, 6);
  assert.doesNotMatch(notifications, /<input[^>]+type="checkbox"/s);
});

test('switch visuals are centralized and driven by semantic accent tokens', async () => {
  const source = await readSource('src/styles/components.css');

  assert.match(source, /\.ui-switch__track\s*\{[^}]*width:\s*46px;[^}]*height:\s*26px;/s);
  assert.match(source, /\.ui-switch__input:checked \+ \.ui-switch__track\s*\{[^}]*background:\s*var\(--ui-accent\);/s);
  assert.match(source, /\.ui-switch__input:focus-visible \+ \.ui-switch__track\s*\{[^}]*box-shadow:\s*0 0 0 3px var\(--ui-focus-ring\);/s);
  assert.match(source, /\.ui-switch\.is-disabled\s*\{[^}]*opacity:/s);
  assert.match(source, /\.ui-checkbox__input:checked \+ \.ui-checkbox__box\s*\{[^}]*background:\s*var\(--ui-accent\);/s);
});

test('compose header fields share one grid on desktop and one column on mobile', async () => {
  const source = await readSource('src/styles/page-system.css');

  assert.match(source, /\.compose-page \.form-row\s*\{[^}]*display:\s*grid[^}]*grid-template-columns:\s*72px minmax\(0, 1fr\) auto;/s);
  assert.match(source, /\.compose-page \.compose-field-label,[\s\S]*\.compose-page \.form-row > \.ui-field__label\s*\{[^}]*text-align:\s*right;/s);
  assert.match(source, /@media \(max-width:\s*768px\)[\s\S]*\.compose-page \.form-row\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)(?:\s*!important)?;/s);
  assert.match(source, /@media \(max-width:\s*768px\)[\s\S]*\.compose-page \.compose-field-label,[\s\S]*text-align:\s*left;/s);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {
  ACCOUNT_ICON_PRESETS,
  accountIconPresetSvg,
  isAccountIconPreset,
} from '../src/utils/account-icon-presets.ts';

test('preset IDs are stable and unknown values are rejected', () => {
  assert.deepEqual(ACCOUNT_ICON_PRESETS.map((item) => item.id), [
    'mail-purple', 'mail-blue', 'mail-green', 'work',
    'personal', 'school', 'team', 'star',
  ]);
  assert.equal(isAccountIconPreset('work'), true);
  assert.equal(isAccountIconPreset('missing'), false);
  assert.match(accountIconPresetSvg('work'), /^<svg/);
  assert.equal(accountIconPresetSvg('missing'), '');
});

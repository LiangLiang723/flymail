import assert from 'node:assert/strict';
import test from 'node:test';

import { isNegativeCssLength } from '../src/utils/mail-body-layout.ts';

test('negative CSS lengths are detected without changing zero or positive spacing', () => {
  assert.equal(isNegativeCssLength('-18.0pt'), true);
  assert.equal(isNegativeCssLength('-.5rem'), true);
  assert.equal(isNegativeCssLength('-12px'), true);
  assert.equal(isNegativeCssLength('0'), false);
  assert.equal(isNegativeCssLength('12px'), false);
  assert.equal(isNegativeCssLength('auto'), false);
  assert.equal(isNegativeCssLength('calc(0px - 12px)'), false);
});

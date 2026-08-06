import test from 'node:test';
import assert from 'node:assert/strict';
import {
  clampImageWidth,
  imageWidthFromPercent,
  parseImageWidth,
} from '../src/utils/editor-image-size.ts';

test('parses positive pixel widths and rejects invalid values', () => {
  assert.equal(parseImageWidth('320'), 320);
  assert.equal(parseImageWidth('320px'), 320);
  assert.equal(parseImageWidth('-10'), null);
  assert.equal(parseImageWidth('auto'), null);
  assert.equal(parseImageWidth(''), null);
});

test('clamps image width to the editor bounds', () => {
  assert.equal(clampImageWidth(20, 600), 80);
  assert.equal(clampImageWidth(320, 600), 320);
  assert.equal(clampImageWidth(900, 600), 600);
  assert.equal(clampImageWidth(80, 48), 48);
});

test('converts quick percentages into bounded integer pixels', () => {
  assert.equal(imageWidthFromPercent(640, 25), 160);
  assert.equal(imageWidthFromPercent(641, 50), 321);
  assert.equal(imageWidthFromPercent(640, 100), 640);
});

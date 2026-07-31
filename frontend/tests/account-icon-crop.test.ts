import test from 'node:test';
import assert from 'node:assert/strict';
import { clampCropState, coverScale, pinchScale } from '../src/utils/account-icon-crop.ts';

test('cover scale fills a square crop viewport', () => {
  assert.equal(coverScale(600, 300, 320), 320 / 300);
  assert.equal(coverScale(300, 600, 320), 320 / 300);
});

test('crop offsets never expose empty canvas', () => {
  const clamped = clampCropState({ scale: 2, offsetX: 999, offsetY: -999 }, 400, 300, 320);
  assert.ok(Number.isFinite(clamped.offsetX));
  assert.ok(Number.isFinite(clamped.offsetY));
  assert.ok(Math.abs(clamped.offsetX) <= (400 * 2 - 320) / 2);
  assert.ok(Math.abs(clamped.offsetY) <= (300 * 2 - 320) / 2);
});

test('pinch zoom is bounded by the crop limits', () => {
  assert.equal(pinchScale(1, 100, 200), 2);
  assert.equal(pinchScale(4, 100, 300), 5);
});

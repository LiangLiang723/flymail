import test from 'node:test';
import assert from 'node:assert/strict';
import { clampScale, nextImageIndex, shouldChangeImageFromSwipe } from '../src/utils/image-viewer.ts';

test('clamps image scale to the supported viewer range', () => {
  assert.equal(clampScale(0.2), 1);
  assert.equal(clampScale(2.5), 2.5);
  assert.equal(clampScale(9), 5);
});

test('cycles through images in both directions', () => {
  assert.equal(nextImageIndex(0, 1, 3), 1);
  assert.equal(nextImageIndex(2, 1, 3), 0);
  assert.equal(nextImageIndex(0, -1, 3), 2);
  assert.equal(nextImageIndex(0, 1, 0), 0);
});

test('recognizes a horizontal swipe used for image navigation', () => {
  assert.equal(shouldChangeImageFromSwipe(90, 12, 300), true);
  assert.equal(shouldChangeImageFromSwipe(45, 5, 200), false);
  assert.equal(shouldChangeImageFromSwipe(90, 120, 300), false);
  assert.equal(shouldChangeImageFromSwipe(90, 12, 900), false);
});

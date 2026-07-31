import test from 'node:test';
import assert from 'node:assert/strict';
import {
  compositeColor,
  contrastRatio,
  ensureContrast,
  parseColor,
  relativeLuminance,
  toHex,
} from '../src/utils/mail-color-contrast.ts';

const WHITE = { r: 255, g: 255, b: 255, a: 1 };
const DARK = { r: 23, g: 24, b: 29, a: 1 };

test('parses supported CSS color forms without a browser', () => {
  assert.deepEqual(parseColor('#123'), { r: 17, g: 34, b: 51, a: 1 });
  assert.deepEqual(parseColor('#11223380'), { r: 17, g: 34, b: 51, a: 128 / 255 });
  assert.deepEqual(parseColor('rgb(10, 20, 30)'), { r: 10, g: 20, b: 30, a: 1 });
  assert.deepEqual(parseColor('rgba(10, 20, 30, 0.5)'), { r: 10, g: 20, b: 30, a: 0.5 });
  assert.deepEqual(parseColor('hsl(0, 100%, 50%)'), { r: 255, g: 0, b: 0, a: 1 });
  assert.equal(parseColor('linear-gradient(red, blue)'), null);
});

test('uses an optional browser resolver for named colors', () => {
  assert.deepEqual(parseColor('rebeccapurple', () => 'rgb(102, 51, 153)'), { r: 102, g: 51, b: 153, a: 1 });
});

test('computes WCAG luminance and contrast', () => {
  assert.equal(relativeLuminance({ r: 0, g: 0, b: 0, a: 1 }), 0);
  assert.equal(relativeLuminance(WHITE), 1);
  assert.equal(contrastRatio({ r: 0, g: 0, b: 0, a: 1 }, WHITE), 21);
});

test('composites transparent colors before contrast checks', () => {
  const result = compositeColor({ r: 255, g: 255, b: 255, a: 0.5 }, { r: 0, g: 0, b: 0, a: 1 });
  assert.deepEqual(result, { r: 128, g: 128, b: 128, a: 1 });
});

test('raises low contrast black text for a dark surface', () => {
  const adjusted = ensureContrast({ r: 0, g: 0, b: 0, a: 1 }, DARK, 4.5, WHITE);
  assert.ok(contrastRatio(adjusted, DARK) >= 4.5);
});

test('darkens low contrast gray text for a light surface', () => {
  const adjusted = ensureContrast({ r: 210, g: 210, b: 210, a: 1 }, WHITE, 4.5, { r: 23, g: 24, b: 29, a: 1 });
  assert.ok(contrastRatio(adjusted, WHITE) >= 4.5);
});

test('preserves colors that already pass', () => {
  const original = { r: 22, g: 124, b: 50, a: 1 };
  assert.ok(contrastRatio(original, WHITE) >= 4.5);
  assert.deepEqual(ensureContrast(original, WHITE), original);
  assert.equal(toHex(original), '#167c32');
});

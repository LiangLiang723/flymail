export interface RgbaColor {
  r: number;
  g: number;
  b: number;
  a: number;
}

export const MIN_MAIL_TEXT_CONTRAST = 4.5;

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));
const byte = (value: number) => Math.round(clamp(value, 0, 255));

function parseHex(value: string): RgbaColor | null {
  const hex = value.slice(1);
  if (![3, 4, 6, 8].includes(hex.length) || !/^[0-9a-f]+$/i.test(hex)) return null;
  const expanded = hex.length <= 4 ? [...hex].map((part) => part + part).join('') : hex;
  return {
    r: Number.parseInt(expanded.slice(0, 2), 16),
    g: Number.parseInt(expanded.slice(2, 4), 16),
    b: Number.parseInt(expanded.slice(4, 6), 16),
    a: expanded.length === 8 ? Number.parseInt(expanded.slice(6, 8), 16) / 255 : 1,
  };
}

function parseChannel(value: string): number | null {
  const text = value.trim();
  const parsed = Number.parseFloat(text);
  if (!Number.isFinite(parsed)) return null;
  return byte(text.endsWith('%') ? parsed * 2.55 : parsed);
}

function parseAlpha(value?: string): number | null {
  if (value === undefined) return 1;
  const text = value.trim();
  const parsed = Number.parseFloat(text);
  if (!Number.isFinite(parsed)) return null;
  return clamp(text.endsWith('%') ? parsed / 100 : parsed, 0, 1);
}

function hslToRgb(hue: number, saturation: number, lightness: number, alpha = 1): RgbaColor {
  const h = ((hue % 360) + 360) % 360;
  const s = clamp(saturation, 0, 1);
  const l = clamp(lightness, 0, 1);
  const chroma = (1 - Math.abs(2 * l - 1)) * s;
  const section = h / 60;
  const x = chroma * (1 - Math.abs((section % 2) - 1));
  let rgb = [0, 0, 0];
  if (section < 1) rgb = [chroma, x, 0];
  else if (section < 2) rgb = [x, chroma, 0];
  else if (section < 3) rgb = [0, chroma, x];
  else if (section < 4) rgb = [0, x, chroma];
  else if (section < 5) rgb = [x, 0, chroma];
  else rgb = [chroma, 0, x];
  const match = l - chroma / 2;
  return { r: byte((rgb[0] + match) * 255), g: byte((rgb[1] + match) * 255), b: byte((rgb[2] + match) * 255), a: alpha };
}

function parseFunctional(value: string): RgbaColor | null {
  const match = value.match(/^(rgba?|hsla?)\((.*)\)$/i);
  if (!match) return null;
  const parts = match[2].replace(/\s*\/\s*/, ',').split(/\s*,\s*|\s+/).filter(Boolean);
  if (parts.length < 3 || parts.length > 4) return null;
  const alpha = parseAlpha(parts[3]);
  if (alpha === null) return null;
  if (match[1].toLowerCase().startsWith('rgb')) {
    const channels = parts.slice(0, 3).map(parseChannel);
    return channels.some((part) => part === null) ? null : { r: channels[0]!, g: channels[1]!, b: channels[2]!, a: alpha };
  }
  if (!parts[1].endsWith('%') || !parts[2].endsWith('%')) return null;
  const hue = Number.parseFloat(parts[0]);
  const saturation = Number.parseFloat(parts[1]) / 100;
  const lightness = Number.parseFloat(parts[2]) / 100;
  return [hue, saturation, lightness].every(Number.isFinite) ? hslToRgb(hue, saturation, lightness, alpha) : null;
}

export function parseColor(value: string, browserResolver?: (value: string) => string): RgbaColor | null {
  const normalized = (value || '').trim().toLowerCase();
  if (!normalized || normalized === 'transparent' || /gradient\(|url\(/i.test(normalized)) return null;
  if (normalized.startsWith('#')) return parseHex(normalized);
  const functional = parseFunctional(normalized);
  if (functional) return functional;
  if (!browserResolver) return null;
  const resolved = browserResolver(value).trim().toLowerCase();
  return resolved && resolved !== normalized ? parseColor(resolved) : null;
}

export function compositeColor(foreground: RgbaColor, background: RgbaColor): RgbaColor {
  const fa = clamp(foreground.a, 0, 1);
  const ba = clamp(background.a, 0, 1);
  const alpha = fa + ba * (1 - fa);
  if (!alpha) return { r: 0, g: 0, b: 0, a: 0 };
  return {
    r: byte((foreground.r * fa + background.r * ba * (1 - fa)) / alpha),
    g: byte((foreground.g * fa + background.g * ba * (1 - fa)) / alpha),
    b: byte((foreground.b * fa + background.b * ba * (1 - fa)) / alpha),
    a: alpha,
  };
}

export function relativeLuminance(color: RgbaColor): number {
  const channels = [color.r, color.g, color.b].map((channel) => {
    const value = clamp(channel, 0, 255) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

export function contrastRatio(foreground: RgbaColor, background: RgbaColor): number {
  const backgroundSolid = background.a < 1 ? compositeColor(background, { r: 255, g: 255, b: 255, a: 1 }) : background;
  const foregroundSolid = foreground.a < 1 ? compositeColor(foreground, backgroundSolid) : foreground;
  const first = relativeLuminance(foregroundSolid);
  const second = relativeLuminance(backgroundSolid);
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
}

function rgbToHsl(color: RgbaColor): { h: number; s: number; l: number } {
  const [r, g, b] = [color.r, color.g, color.b].map((channel) => channel / 255);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const delta = max - min;
  const l = (max + min) / 2;
  if (!delta) return { h: 0, s: 0, l };
  const s = delta / (1 - Math.abs(2 * l - 1));
  let h = max === r ? 60 * (((g - b) / delta) % 6) : max === g ? 60 * ((b - r) / delta + 2) : 60 * ((r - g) / delta + 4);
  if (h < 0) h += 360;
  return { h, s, l };
}

function search(original: RgbaColor, background: RgbaColor, minimum: number, target: 0 | 1): RgbaColor | null {
  const hsl = rgbToHsl(original);
  const extreme = hslToRgb(hsl.h, hsl.s, target);
  if (contrastRatio(extreme, background) < minimum) return null;
  let low = Math.min(hsl.l, target);
  let high = Math.max(hsl.l, target);
  let best = extreme;
  for (let index = 0; index < 24; index += 1) {
    const middle = (low + high) / 2;
    const candidate = hslToRgb(hsl.h, hsl.s, middle);
    if (contrastRatio(candidate, background) >= minimum) {
      best = candidate;
      if (target === 0) low = middle;
      else high = middle;
    } else if (target === 0) high = middle;
    else low = middle;
  }
  return best;
}

const distance = (left: RgbaColor, right: RgbaColor) => Math.hypot(left.r - right.r, left.g - right.g, left.b - right.b);

export function ensureContrast(
  foreground: RgbaColor,
  background: RgbaColor,
  minimum = MIN_MAIL_TEXT_CONTRAST,
  fallback: RgbaColor = { r: 0, g: 0, b: 0, a: 1 },
): RgbaColor {
  const solid = foreground.a < 1 ? compositeColor(foreground, background) : { ...foreground, a: 1 };
  if (contrastRatio(solid, background) >= minimum) return solid;
  const darker = search(solid, background, minimum, 0);
  const lighter = search(solid, background, minimum, 1);
  if (darker && lighter) return distance(solid, darker) <= distance(solid, lighter) ? darker : lighter;
  if (darker) return darker;
  if (lighter) return lighter;
  return contrastRatio(fallback, background) >= minimum ? { ...fallback, a: 1 } : relativeLuminance(background) > 0.5
    ? { r: 0, g: 0, b: 0, a: 1 }
    : { r: 255, g: 255, b: 255, a: 1 };
}

export function toHex(color: RgbaColor): string {
  return `#${[color.r, color.g, color.b].map((channel) => byte(channel).toString(16).padStart(2, '0')).join('')}`;
}

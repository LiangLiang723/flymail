import {
  compositeColor,
  ensureContrast,
  parseColor,
  toHex,
  type RgbaColor,
} from './mail-color-contrast';

export const MAX_ADAPTED_MAIL_NODES = 8000;

const DEFAULT_LIGHT_BACKGROUND: RgbaColor = { r: 255, g: 255, b: 255, a: 1 };
const DEFAULT_DARK_BACKGROUND: RgbaColor = { r: 23, g: 24, b: 29, a: 1 };
const DEFAULT_LIGHT_TEXT: RgbaColor = { r: 23, g: 24, b: 29, a: 1 };
const DEFAULT_DARK_TEXT: RgbaColor = { r: 245, g: 245, b: 247, a: 1 };
const MEDIA_ELEMENTS = new Set(['IMG', 'PICTURE', 'SOURCE', 'SVG', 'CANVAS']);

export interface MailThemeOptions {
  lightBackground?: RgbaColor;
  darkBackground?: RgbaColor;
  lightText?: RgbaColor;
  darkText?: RgbaColor;
  maximumNodes?: number;
}

interface ThemeBackgrounds {
  light: RgbaColor;
  dark: RgbaColor;
}

function browserColorResolver(value: string): string {
  if (typeof document === 'undefined') return '';
  const canvas = document.createElement('canvas');
  const context = canvas.getContext('2d');
  if (!context) return '';
  context.fillStyle = '#000000';
  context.fillStyle = value;
  return String(context.fillStyle || '');
}

function explicitForeground(element: HTMLElement): string {
  return element.style.color || element.getAttribute('color') || '';
}

function explicitBackground(element: HTMLElement): string {
  const styleColor = element.style.backgroundColor;
  if (styleColor) return styleColor;
  const background = element.style.background.trim();
  if (background && !/gradient\(|url\(|\s+\/\s+/i.test(background)) {
    const parsed = parseColor(background, browserColorResolver);
    if (parsed) return background;
  }
  return element.getAttribute('bgcolor') || '';
}

function removeForeground(element: HTMLElement) {
  element.style.removeProperty('color');
  element.removeAttribute('color');
}

function removeBackground(element: HTMLElement) {
  element.style.removeProperty('background-color');
  element.style.removeProperty('background');
  element.removeAttribute('bgcolor');
}

function backgroundForElement(
  element: HTMLElement,
  backgrounds: WeakMap<HTMLElement, ThemeBackgrounds>,
  rootBackgrounds: ThemeBackgrounds,
): ThemeBackgrounds {
  const parent = element.parentElement;
  return (parent && backgrounds.get(parent)) || rootBackgrounds;
}

function resolveBackgrounds(raw: string, inherited: ThemeBackgrounds): ThemeBackgrounds | null {
  const parsed = parseColor(raw, browserColorResolver);
  if (!parsed) return null;
  if (parsed.a >= 1) return { light: parsed, dark: parsed };
  return {
    light: compositeColor(parsed, inherited.light),
    dark: compositeColor(parsed, inherited.dark),
  };
}

export function adaptMailBodyColors(sanitizedHtml: string, options: MailThemeOptions = {}): string {
  if (!sanitizedHtml || typeof DOMParser === 'undefined') return sanitizedHtml;
  const rootBackgrounds = {
    light: options.lightBackground || DEFAULT_LIGHT_BACKGROUND,
    dark: options.darkBackground || DEFAULT_DARK_BACKGROUND,
  };
  const lightText = options.lightText || DEFAULT_LIGHT_TEXT;
  const darkText = options.darkText || DEFAULT_DARK_TEXT;
  const maximumNodes = options.maximumNodes ?? MAX_ADAPTED_MAIL_NODES;

  try {
    const parsedDocument = new DOMParser().parseFromString(`<body>${sanitizedHtml}</body>`, 'text/html');
    const elements = Array.from(parsedDocument.body.querySelectorAll<HTMLElement>('*'));
    const backgrounds = new WeakMap<HTMLElement, ThemeBackgrounds>();

    elements.forEach((element, index) => {
      if (MEDIA_ELEMENTS.has(element.tagName)) return;
      const inherited = backgroundForElement(element, backgrounds, rootBackgrounds);
      const rawBackground = explicitBackground(element);
      const resolvedBackground = rawBackground ? resolveBackgrounds(rawBackground, inherited) : null;
      const effectiveBackground = resolvedBackground || inherited;
      backgrounds.set(element, effectiveBackground);

      if (resolvedBackground && parseColor(rawBackground, browserColorResolver)?.a! < 1) {
        removeBackground(element);
        element.classList.add('flymail-mail-background');
        element.style.setProperty('--flymail-mail-background-light', toHex(resolvedBackground.light));
        element.style.setProperty('--flymail-mail-background-dark', toHex(resolvedBackground.dark));
      }

      const rawForeground = explicitForeground(element);
      if (!rawForeground) return;
      removeForeground(element);

      if (index >= maximumNodes) {
        element.classList.add('flymail-mail-color-fallback');
        return;
      }

      const foreground = parseColor(rawForeground, browserColorResolver);
      if (!foreground) {
        element.classList.add('flymail-mail-color-fallback');
        return;
      }

      const adjustedLight = ensureContrast(foreground, effectiveBackground.light, 4.5, lightText);
      const adjustedDark = ensureContrast(foreground, effectiveBackground.dark, 4.5, darkText);
      element.classList.add('flymail-mail-color');
      element.style.setProperty('--flymail-mail-color-light', toHex(adjustedLight));
      element.style.setProperty('--flymail-mail-color-dark', toHex(adjustedDark));
    });

    return parsedDocument.body.innerHTML;
  } catch {
    console.warn('mail body theme adaptation failed');
    return sanitizedHtml;
  }
}

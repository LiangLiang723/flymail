import createDOMPurify from 'dompurify';

import type { SanitizedMailBody } from '../../entities/message/types.ts';

interface SanitizeOptions {
  window?: unknown;
  allowRemoteImages?: boolean;
}

const FORBIDDEN_TAGS = [
  'script', 'style', 'form', 'input', 'textarea', 'select', 'option', 'button',
  'iframe', 'object', 'embed', 'applet', 'meta', 'link', 'base', 'svg', 'math',
];
const FORBIDDEN_ATTRIBUTES = ['style', 'srcdoc', 'formaction', 'xlink:href'];

function runtimeWindow(explicit?: unknown): Window {
  const candidate = explicit || (typeof window !== 'undefined' ? window : undefined);
  if (!candidate) throw new Error('mail sanitizer requires a DOM window');
  return candidate as Window;
}

function isSafeHref(value: string): boolean {
  const normalized = value.trim().toLowerCase();
  return normalized.startsWith('https://')
    || normalized.startsWith('http://')
    || normalized.startsWith('mailto:')
    || normalized.startsWith('tel:')
    || normalized.startsWith('#');
}

function isSafeLocalImage(value: string): boolean {
  const normalized = value.trim().toLowerCase();
  return normalized.startsWith('cid:')
    || normalized.startsWith('/api/')
    || /^data:image\/(?:png|jpe?g|gif|webp);base64,/.test(normalized);
}

function isRemoteImage(value: string): boolean {
  return /^https?:\/\//i.test(value.trim());
}

export function safeLinkDomain(value: string): string {
  try {
    const url = new URL(value);
    return url.hostname;
  } catch {
    return '';
  }
}

export function sanitizeMailHtml(source: string, options: SanitizeOptions = {}): SanitizedMailBody {
  const domWindow = runtimeWindow(options.window);
  const purify = createDOMPurify(domWindow as never);
  const preflight = domWindow.document.createElement('template');
  preflight.innerHTML = String(source || '');
  preflight.content.querySelectorAll(FORBIDDEN_TAGS.join(',')).forEach((node) => node.remove());
  const cleaned = purify.sanitize(preflight.innerHTML, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: FORBIDDEN_TAGS,
    FORBID_ATTR: FORBIDDEN_ATTRIBUTES,
    ALLOW_DATA_ATTR: true,
    ALLOW_ARIA_ATTR: true,
  });
  const template = domWindow.document.createElement('template');
  template.innerHTML = String(cleaned);
  template.content.querySelectorAll(FORBIDDEN_TAGS.join(',')).forEach((node) => node.remove());
  const blockedRemoteImages: string[] = [];

  for (const element of Array.from(template.content.querySelectorAll('*'))) {
    for (const attribute of Array.from(element.attributes)) {
      if (/^on/i.test(attribute.name)) element.removeAttribute(attribute.name);
    }
  }

  for (const anchor of Array.from(template.content.querySelectorAll('a'))) {
    const href = anchor.getAttribute('href') || '';
    if (!isSafeHref(href)) {
      anchor.removeAttribute('href');
      anchor.removeAttribute('target');
      anchor.removeAttribute('rel');
      continue;
    }
    const domain = safeLinkDomain(href);
    if (domain) anchor.setAttribute('data-link-domain', domain);
    anchor.setAttribute('target', '_blank');
    anchor.setAttribute('rel', 'noopener noreferrer nofollow');
  }

  for (const image of Array.from(template.content.querySelectorAll('img'))) {
    const src = image.getAttribute('src') || '';
    if (isSafeLocalImage(src)) {
      image.setAttribute('loading', 'lazy');
      image.setAttribute('decoding', 'async');
      continue;
    }
    if (isRemoteImage(src) && options.allowRemoteImages) {
      image.setAttribute('loading', 'lazy');
      image.setAttribute('decoding', 'async');
      image.setAttribute('referrerpolicy', 'no-referrer');
      continue;
    }
    if (isRemoteImage(src)) blockedRemoteImages.push(src);
    image.removeAttribute('src');
    image.removeAttribute('srcset');
    image.setAttribute('data-remote-image-blocked', 'true');
    image.setAttribute('alt', image.getAttribute('alt') || '远程图片已阻止');
  }

  return { html: template.innerHTML, blockedRemoteImages };
}

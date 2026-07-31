# Mail Body Theme Contrast Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Guarantee readable HTML email text in both FlyMail light and dark themes with a WCAG contrast target of `4.5:1`, without modifying stored mail, images, replies, forwards, or PDF exports.

**Architecture:** Keep `sanitizeHtml()` and `renderMailBody()` theme-neutral. Add a pure color-math module plus a browser-only adapter that transforms already-sanitized reading HTML into light/dark CSS-variable pairs. Only `MailList.vue` and `Backup.vue` use the themed renderer; reply/forward and PDF paths continue using the neutral renderer.

**Tech Stack:** Vue 3, TypeScript, DOMPurify, DOMParser, CSS custom properties, Node test runner, Vite.

## Global Constraints

- Work only in `/home/chatgpt/flymail` on branch `main`.
- Do not add or upgrade production dependencies.
- Keep `renderMailBody()` free of FlyMail theme variables because replies, forwards, and PDF export consume it.
- Do not change stored `body_html` or `body_text`.
- Do not apply filters, inversion, or color rewriting to images and logos.
- Target contrast is `4.5:1` for all email text; do not apply a `3:1` large-text exception.
- Support light theme, dark theme, invalid colors, transparent colors, explicit element backgrounds, and legacy `font color` / `bgcolor`.
- Color adaptation must fail open to sanitized readable content, never to an empty message.
- The combined feature release target is the next patch version after `0.0.23`; version changes happen in the account-icon plan’s final release task.

---

### Task 1: Build the Pure Contrast Engine

**Files:**
- Create: `frontend/src/utils/mail-color-contrast.ts`
- Create: `frontend/tests/mail-color-contrast.test.ts`

**Interfaces:**
- Produces: `type RgbaColor = { r: number; g: number; b: number; a: number }` with RGB channels in `0..255` and alpha in `0..1`.
- Produces: `parseColor(value: string, browserResolver?: (value: string) => string): RgbaColor | null`.
- Produces: `compositeColor(foreground: RgbaColor, background: RgbaColor): RgbaColor`.
- Produces: `relativeLuminance(color: RgbaColor): number`.
- Produces: `contrastRatio(foreground: RgbaColor, background: RgbaColor): number`.
- Produces: `ensureContrast(foreground: RgbaColor, background: RgbaColor, minimum?: number, fallback?: RgbaColor): RgbaColor`.
- Produces: `toHex(color: RgbaColor): string`.

- [ ] **Step 1: Write failing contrast tests**

Create `frontend/tests/mail-color-contrast.test.ts`:

```ts
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
  assert.equal(parseColor('linear-gradient(red, blue)'), null);
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
  const original = { r: 36, g: 138, b: 61, a: 1 };
  assert.deepEqual(ensureContrast(original, WHITE), original);
  assert.equal(toHex(original), '#248a3d');
});
```

- [ ] **Step 2: Run the test and confirm RED**

Run:

```bash
cd frontend
npm test -- --test-name-pattern="parses supported CSS|computes WCAG|composites transparent|raises low contrast|darkens low contrast|preserves colors"
```

Expected: FAIL because `mail-color-contrast.ts` does not exist.

- [ ] **Step 3: Implement the pure module**

Implement `mail-color-contrast.ts` with these rules:

```ts
export const MIN_MAIL_TEXT_CONTRAST = 4.5;

export function relativeLuminance(color: RgbaColor): number {
  const linear = [color.r, color.g, color.b].map((value) => {
    const channel = value / 255;
    return channel <= 0.04045
      ? channel / 12.92
      : Math.pow((channel + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

export function contrastRatio(foreground: RgbaColor, background: RgbaColor): number {
  const fg = relativeLuminance(compositeColor(foreground, background));
  const bg = relativeLuminance(background);
  return (Math.max(fg, bg) + 0.05) / (Math.min(fg, bg) + 0.05);
}
```

`ensureContrast()` must preserve hue and saturation by converting to HSL, binary-searching lightness toward `0` and `1`, and choosing the passing candidate with the smallest lightness delta. If neither candidate passes, return the supplied fallback. Clamp all channels and round serialized RGB values.

`parseColor()` must support hex, `rgb[a]()` and `hsl[a]()` directly. For CSS named colors, use the optional browser resolver; invalid colors, gradients, `currentColor`, `inherit`, `initial`, `unset` and `transparent` return `null` unless alpha composition has a meaningful foreground value.

- [ ] **Step 4: Run the contrast tests and confirm GREEN**

Run the same targeted command. Expected: all six tests PASS.

- [ ] **Step 5: Commit the contrast engine**

```bash
git add frontend/src/utils/mail-color-contrast.ts frontend/tests/mail-color-contrast.test.ts
git diff --staged
git commit -m "✨ 新增邮件正文对比度转换引擎"
```

---

### Task 2: Add the Sanitized HTML Theme Adapter

**Files:**
- Create: `frontend/src/utils/mail-body-theme.ts`
- Create: `frontend/tests/mail-body-theme-contract.test.mjs`
- Modify: `frontend/src/utils/sanitize.ts`
- Modify: `frontend/src/styles/tokens.css`
- Modify: `frontend/src/styles/page-system.css`

**Interfaces:**
- Consumes: all exports from `mail-color-contrast.ts`.
- Produces: `adaptMailBodyColors(sanitizedHtml: string, options?: MailThemeOptions): string`.
- Produces: `renderThemedMailBody(bodyHtml, bodyText): string` from `sanitize.ts`.
- Produces CSS classes `.flymail-mail-color`, `.flymail-mail-background`, and `.flymail-mail-color-fallback` scoped under `.detail-content`.
- Produces tokens `--ui-mail-body-bg-light`, `--ui-mail-body-bg-dark`, `--ui-mail-body-text-light`, and `--ui-mail-body-text-dark`, present in both root theme states.

- [ ] **Step 1: Write the failing integration contract**

Create `frontend/tests/mail-body-theme-contract.test.mjs`:

```js
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), 'utf8');

test('themed mail rendering stays separate from neutral rendering', async () => {
  const sanitize = await read('src/utils/sanitize.ts');
  const mail = await read('src/views/MailList.vue');
  const backup = await read('src/views/Backup.vue');
  const reply = await read('src/composables/useReplyForward.ts');
  const pdf = await read('src/utils/export-pdf.ts');

  assert.match(sanitize, /export function renderThemedMailBody/);
  assert.match(mail, /renderThemedMailBody/);
  assert.match(backup, /renderThemedMailBody/);
  assert.match(reply, /renderMailBody/);
  assert.doesNotMatch(reply, /renderThemedMailBody/);
  assert.match(pdf, /renderMailBody/);
  assert.doesNotMatch(pdf, /renderThemedMailBody/);
});

test('mail theme variables are scoped to reading content', async () => {
  const tokens = await read('src/styles/tokens.css');
  const styles = await read('src/styles/page-system.css');
  assert.match(tokens, /--ui-mail-body-bg-light:/);
  assert.match(tokens, /--ui-mail-body-bg-dark:/);
  assert.match(styles, /\.detail-content \.flymail-mail-color/);
  assert.match(styles, /:root\.dark \.detail-content \.flymail-mail-color/);
  assert.match(styles, /--flymail-mail-color-light/);
  assert.match(styles, /--flymail-mail-color-dark/);
});
```

- [ ] **Step 2: Run the contract test and confirm RED**

```bash
cd frontend
npm test -- --test-name-pattern="themed mail rendering|mail theme variables"
```

Expected: FAIL because the themed renderer and CSS contracts do not exist.

- [ ] **Step 3: Implement `adaptMailBodyColors()`**

Use a detached `DOMParser` document, then perform one `querySelectorAll('*')` traversal. For each element:

1. Read explicit foreground from `style.color`, `color`, or `font[color]`.
2. Read explicit background from `style.backgroundColor`, a pure-color `style.background`, or `bgcolor`.
3. Resolve inherited explicit backgrounds by walking parents; otherwise use the light/dark body tokens.
4. Parse and alpha-composite colors.
5. Generate light/dark adjusted foregrounds with `ensureContrast()`.
6. Remove legacy `color` attributes and the inline `color` declaration after copying safe non-color declarations.
7. Add:

```ts
element.classList.add('flymail-mail-color');
element.style.setProperty('--flymail-mail-color-light', toHex(lightColor));
element.style.setProperty('--flymail-mail-color-dark', toHex(darkColor));
```

For explicit backgrounds, preserve safe colors and add parallel background variables only when transparency requires theme-specific composition. Ignore gradients and background images. Do not touch `IMG`, `PICTURE`, `SOURCE`, `SVG`, or `CANVAS` color data.

Set `MAX_ADAPTED_MAIL_NODES = 8000`. After that threshold, continue the same traversal without expensive parsing: remove explicit text color from remaining nodes and add `flymail-mail-color-fallback` so theme text remains readable.

Wrap the adapter in `try/catch`; on failure return the original sanitized HTML and log only a fixed message such as `console.warn('mail body theme adaptation failed')`, never the email content.

- [ ] **Step 4: Add the reading-only renderer and CSS**

In `sanitize.ts`:

```ts
export function renderThemedMailBody(
  bodyHtml: string | undefined | null,
  bodyText: string | undefined | null = '',
): string {
  return adaptMailBodyColors(renderMailBody(bodyHtml, bodyText));
}
```

In `tokens.css`, add stable light/dark body values available in both modes:

```css
--ui-mail-body-bg-light: #ffffff;
--ui-mail-body-bg-dark: #17181d;
--ui-mail-body-text-light: #17181d;
--ui-mail-body-text-dark: #f5f5f7;
```

In `page-system.css`:

```css
.detail-content .flymail-mail-color {
  color: var(--flymail-mail-color-light) !important;
}

:root.dark .detail-content .flymail-mail-color {
  color: var(--flymail-mail-color-dark) !important;
}

.detail-content .flymail-mail-color-fallback {
  color: var(--ui-mail-body-text-light) !important;
}

:root.dark .detail-content .flymail-mail-color-fallback {
  color: var(--ui-mail-body-text-dark) !important;
}
```

Add equivalent scoped background rules. Keep link underline behavior and do not add global selectors outside `.detail-content`.

- [ ] **Step 5: Run targeted tests and build**

```bash
cd frontend
npm test -- --test-name-pattern="WCAG|contrast|themed mail rendering|mail theme variables"
npm run build
```

Expected: targeted tests PASS; `vue-tsc` and Vite build PASS.

- [ ] **Step 6: Commit the theme adapter**

```bash
git add frontend/src/utils/mail-body-theme.ts frontend/src/utils/sanitize.ts frontend/src/styles/tokens.css frontend/src/styles/page-system.css frontend/tests/mail-body-theme-contract.test.mjs
git diff --staged
git commit -m "🎨 增加邮件正文双主题颜色适配"
```

---

### Task 3: Wire Mail and Backup Reading Views

**Files:**
- Modify: `frontend/src/views/MailList.vue`
- Modify: `frontend/src/views/Backup.vue`
- Modify: `frontend/tests/profile-and-image-viewer.test.mjs`
- Modify: `frontend/tests/mail-body-theme-contract.test.mjs`

**Interfaces:**
- Consumes: `renderThemedMailBody(bodyHtml, bodyText)`.
- Preserves: `handleMailLinkClick`, image viewer click handling, attachment rendering, reply/forward and export behavior.

- [ ] **Step 1: Extend the failing view contract**

Add assertions that `MailList.vue` and `Backup.vue` import and call `renderThemedMailBody`, while `useReplyForward.ts` and `export-pdf.ts` remain neutral. Also assert neither reading view uses CSS `filter: invert` on `.detail-content img`.

- [ ] **Step 2: Run the view contract and confirm RED**

```bash
cd frontend
npm test -- --test-name-pattern="themed mail rendering stays separate"
```

Expected: FAIL until both views are rewired.

- [ ] **Step 3: Rewire the two reading views**

In `MailList.vue`, replace the import and function call:

```ts
import { renderThemedMailBody } from '../utils/sanitize';

function renderMessageBody(message: Message | null) {
  if (!message) return '';
  return renderThemedMailBody(message.body_html, message.body_text);
}
```

In `Backup.vue`, keep `handleMailLinkClick` and replace only the rendered HTML function:

```vue
<div
  v-if="detailData.body_html || detailData.body_text"
  class="detail-content"
  v-html="renderThemedMailBody(detailData.body_html, detailData.body_text)"
/>
```

- [ ] **Step 4: Verify reading behavior contracts**

```bash
cd frontend
npm test -- --test-name-pattern="themed mail rendering|mail detail hides inline assets|image viewer"
npm run build
```

Expected: all selected tests and build PASS.

- [ ] **Step 5: Commit the view wiring**

```bash
git add frontend/src/views/MailList.vue frontend/src/views/Backup.vue frontend/tests/mail-body-theme-contract.test.mjs frontend/tests/profile-and-image-viewer.test.mjs
git diff --staged
git commit -m "🐛 修复邮件正文在深浅主题下不可读"
```

---

### Task 4: Complete Contrast Verification

**Files:**
- Verify only: all frontend tests, production build, Shell syntax, Compose configuration and Git diff checks
- Documentation and version changes are deferred to Task 8 of `2026-07-31-account-icon-customization.md`

**Interfaces:**
- Produces a completed, independently testable mail contrast feature.
- Does not bump the release version; combined release is handled after the account-icon plan.

- [ ] **Step 1: Run the complete frontend suite**

```bash
cd frontend
npm test
npm run build
```

Expected: all tests PASS; production build PASS with no new TypeScript errors.

- [ ] **Step 2: Run repository checks**

```bash
bash -n scripts/docker-entrypoint.sh
docker compose --env-file .env.example config >/dev/null
git diff --check
git status --short
git diff
```

If Compose still requires the service-level `.env`, use the project’s existing read-only configuration-stream check; do not create or commit `.env`.

- [ ] **Step 3: Review the exact acceptance fixtures**

Confirm automated fixtures cover:

- black text on `#17181d` reaches `4.5:1`;
- light gray text on white reaches `4.5:1`;
- passing green brand text is unchanged;
- text inside explicit dark and light backgrounds uses that background;
- `font color`, inline `color`, `bgcolor`, RGBA and invalid colors;
- images retain their original `src`, style and pixels;
- theme switching selects CSS variables without rerendering or refetching mail.

- [ ] **Step 4: Leave documentation and version unchanged**

Confirm `git status --short` contains only implementation files from Tasks 1–3. Do not edit README or version files in this plan; Task 8 of the account-icon plan documents and releases both features together.

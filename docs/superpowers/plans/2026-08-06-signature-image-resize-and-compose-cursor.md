# 签名图片缩放与写信光标修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让签名图片尺寸可调整并持久化，同时保证新建、回复和转发应用签名后光标位于顶部正文区，正文与签名之间有空行，切换签名不会删除用户文字。

**Architecture:** 在独立纯函数模块中集中处理图片宽度限制和百分比换算；扩展 Tiptap Image 节点的 `width` 属性并使用原生 NodeView 实现选中、拖拽和快捷尺寸。签名块继续由 `TiptapEditor.vue` 统一管理，但改为原子隔离节点，并通过单个 ProseMirror transaction 删除旧签名、创建正文/分隔段、插入新签名和设置顶部选区。回复与转发生成的原邮件区域增加稳定标记，供重新插入签名时确定引用边界。

**Tech Stack:** Vue 3、TypeScript、Tiptap 3、ProseMirror、Node.js Test Runner、Vite、Playwright、Docker。

## Global Constraints

- 目标版本固定为 `0.0.37`。
- 不新增或升级生产依赖。
- 不修改签名图片上传接口、存储目录或公开访问规则。
- 不修改数据库结构，不迁移或删除 `/Docker/flymail/data`。
- 图片宽度最小 `80px`，最大为编辑器实际可用宽度，保存整数像素。
- 草稿不自动应用默认签名，不自动重排已有 HTML。
- 只删除 `signatureBlock` 节点，任何签名块外的正文必须逐字保留。
- 修改前端应用后必须构建镜像、运行独立临时容器并替换当前 `flymail` 容器。

---

### Task 1: 图片宽度纯函数与可持久化 Image 节点

**Files:**
- Create: `frontend/src/utils/editor-image-size.ts`
- Create: `frontend/tests/editor-image-size.test.ts`
- Modify: `frontend/src/components/TiptapEditor.vue`
- Modify: `frontend/tests/signature-image-upload.test.mjs`

**Interfaces:**
- Produces: `parseImageWidth(value: unknown): number | null`
- Produces: `clampImageWidth(value: number, containerWidth: number, minimum?: number): number`
- Produces: `imageWidthFromPercent(containerWidth: number, percent: number, minimum?: number): number`
- Produces: Tiptap 节点属性 `width: number | null`，HTML 以 `<img width="320">` 持久化。

- [ ] **Step 1: Write the failing utility tests**

```ts
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
```

- [ ] **Step 2: Run the test and verify RED**

Run: `cd frontend && node --test tests/editor-image-size.test.ts`

Expected: FAIL because `src/utils/editor-image-size.ts` does not exist.

- [ ] **Step 3: Implement the pure functions**

```ts
export const MIN_EDITOR_IMAGE_WIDTH = 80;

export function parseImageWidth(value: unknown): number | null {
  const match = String(value ?? '').trim().match(/^(\d+)(?:px)?$/i);
  if (!match) return null;
  const width = Number(match[1]);
  return Number.isFinite(width) && width > 0 ? Math.round(width) : null;
}

export function clampImageWidth(
  value: number,
  containerWidth: number,
  minimum = MIN_EDITOR_IMAGE_WIDTH,
): number {
  const safeContainer = Math.max(1, Math.round(containerWidth || 0));
  const safeMinimum = Math.min(Math.max(1, Math.round(minimum)), safeContainer);
  return Math.min(safeContainer, Math.max(safeMinimum, Math.round(value || safeMinimum)));
}

export function imageWidthFromPercent(
  containerWidth: number,
  percent: number,
  minimum = MIN_EDITOR_IMAGE_WIDTH,
): number {
  return clampImageWidth(containerWidth * percent / 100, containerWidth, minimum);
}
```

- [ ] **Step 4: Extend the Image node and update its contract test**

In `TiptapEditor.vue`, replace the stock `Image` extension with `ResizableImage = Image.extend(...)`:

```ts
const ResizableImage = Image.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      width: {
        default: null,
        parseHTML: (element: HTMLElement) => (
          parseImageWidth(element.getAttribute('width'))
          ?? parseImageWidth(element.style.width)
        ),
        renderHTML: (attributes: Record<string, any>) => (
          attributes.width ? { width: String(Math.round(attributes.width)) } : {}
        ),
      },
    };
  },
});
```

Update `signature-image-upload.test.mjs` to assert `ResizableImage`, `parseImageWidth`, and `renderHTML` width persistence are present.

- [ ] **Step 5: Run focused tests and build**

Run: `cd frontend && node --test tests/editor-image-size.test.ts tests/signature-image-upload.test.mjs && npm run build`

Expected: utility tests and image contract tests PASS; Vue typecheck and Vite build PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/utils/editor-image-size.ts frontend/tests/editor-image-size.test.ts frontend/src/components/TiptapEditor.vue frontend/tests/signature-image-upload.test.mjs
git commit -m "🖼️ 持久化签名图片显示宽度"
```

### Task 2: 图片选择、拖拽手柄与快捷尺寸

**Files:**
- Create: `frontend/src/utils/resizable-image-node-view.ts`
- Create: `frontend/tests/resizable-image-node-view.test.mjs`
- Modify: `frontend/src/components/TiptapEditor.vue`

**Interfaces:**
- Consumes: `clampImageWidth()` and `imageWidthFromPercent()` from Task 1.
- Produces: `createResizableImageNodeView(options)`，返回 Tiptap `addNodeView` 可用的 NodeView。
- Produces DOM classes: `.resizable-image-node`, `.resizable-image-node--selected`, `.image-resize-handle`, `.image-size-toolbar`。

- [ ] **Step 1: Write the failing NodeView contract test**

```js
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), 'utf8');

test('resizable image node view exposes drag and keyboard-accessible quick sizes', async () => {
  const source = await read('src/utils/resizable-image-node-view.ts');
  assert.match(source, /aria-label.*调整图片大小/);
  assert.match(source, /\[25, 50, 75, 100\]/);
  assert.match(source, /pointermove/);
  assert.match(source, /setNodeMarkup/);
  assert.match(source, /imageWidthFromPercent/);
});
```

- [ ] **Step 2: Run the contract test and verify RED**

Run: `cd frontend && node --test tests/resizable-image-node-view.test.mjs`

Expected: FAIL because the NodeView module does not exist.

- [ ] **Step 3: Implement the native NodeView**

`createResizableImageNodeView` must:

```ts
export function createResizableImageNodeView({ node: initialNode, editor, getPos }: any) {
  let node = initialNode;
  const dom = document.createElement('div');
  const image = document.createElement('img');
  const handle = document.createElement('button');
  const toolbar = document.createElement('div');

  dom.className = 'resizable-image-node';
  dom.contentEditable = 'false';
  handle.type = 'button';
  handle.className = 'image-resize-handle';
  handle.setAttribute('aria-label', '调整图片大小');

  for (const percent of [25, 50, 75, 100]) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = `${percent}%`;
    button.addEventListener('mousedown', (event) => event.preventDefault());
    button.addEventListener('click', () => updateWidth(imageWidthFromPercent(editor.view.dom.clientWidth, percent)));
    toolbar.append(button);
  }

  function updateWidth(width: number) {
    const pos = typeof getPos === 'function' ? getPos() : null;
    if (typeof pos !== 'number') return;
    editor.view.dispatch(editor.state.tr.setNodeMarkup(pos, undefined, { ...node.attrs, width }));
  }

  // pointerdown/pointermove/pointerup use clampImageWidth and preserve aspect ratio by changing width only.
  // selectNode/deselectNode toggle the selected class; update refreshes src, alt, title and width.

  return { dom, update, selectNode, deselectNode, destroy };
}
```

The implementation must register and remove document-level pointer listeners only while dragging.

- [ ] **Step 4: Connect the NodeView to ResizableImage and add styles**

In `TiptapEditor.vue`:

```ts
const ResizableImage = Image.extend({
  // width attribute from Task 1
  addNodeView() {
    return createResizableImageNodeView;
  },
});
```

Add scoped styles for selected border, 12px resize handle, floating quick toolbar, visible keyboard focus, and `max-width: 100%`. The wrapper must remain responsive and the serialized HTML must still be a bare `<img>`.

- [ ] **Step 5: Run focused and full frontend checks**

Run: `cd frontend && node --test tests/editor-image-size.test.ts tests/resizable-image-node-view.test.mjs tests/signature-image-upload.test.mjs && npm test && npm run build`

Expected: all focused tests PASS; complete frontend test count has zero failures; build PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/utils/resizable-image-node-view.ts frontend/tests/resizable-image-node-view.test.mjs frontend/src/components/TiptapEditor.vue
git commit -m "✨ 增加签名图片拖拽与快捷缩放"
```

### Task 3: 签名文档结构、顶部光标与正文保护

**Files:**
- Create: `frontend/tests/compose-signature-layout.test.mjs`
- Modify: `frontend/src/components/TiptapEditor.vue`
- Modify: `frontend/src/composables/useReplyForward.ts`
- Modify: `frontend/tests/compose-signature-contract.test.mjs`
- Modify: `frontend/tests/compose-workspace.test.ts`

**Interfaces:**
- Consumes: parent call `setManagedSignature(signatureId, html, placement)` unchanged.
- Produces: `signatureBlock` is `atom: true`, `isolating: true`, and cannot accept caret text.
- Produces: quote marker `data-flymail-quote="reply|forward"` in newly generated reply and forward HTML.
- Produces: postcondition for non-draft signature application: first node is a body paragraph, exactly one empty separator is before the signature, and selection is at the first body paragraph.

- [ ] **Step 1: Write failing source and behavior contracts**

`compose-signature-layout.test.mjs` must assert:

```js
test('managed signature is atomic and selection returns to the first body paragraph', async () => {
  const source = await read('src/components/TiptapEditor.vue');
  assert.match(source, /name:\s*'signatureBlock'[\s\S]*atom:\s*true/);
  assert.match(source, /TextSelection\.create/);
  assert.match(source, /data-flymail-signature-spacer/);
  assert.match(source, /transaction\.setSelection/);
});

test('reply and forward drafts mark the original quoted area', async () => {
  const source = await read('src/composables/useReplyForward.ts');
  assert.match(source, /data-flymail-quote=\\"reply\\"/);
  assert.match(source, /data-flymail-quote=\\"forward\\"/);
});
```

Update `compose-signature-contract.test.mjs` to require one transaction-based replacement and to reject `chain().focus().insertContentAt(position, signatureHtml)`.

- [ ] **Step 2: Run the tests and verify RED**

Run: `cd frontend && node --test tests/compose-signature-layout.test.mjs tests/compose-signature-contract.test.mjs tests/compose-workspace.test.ts`

Expected: FAIL because the signature node is not atomic, no managed spacer exists, and quote markers are absent.

- [ ] **Step 3: Mark reply and forward quote regions**

Change reply HTML to:

```ts
const quoteHtml = `<p><br></p><blockquote data-flymail-quote="reply" style="border-left:3px solid #ccc;padding-left:10px;color:#666;">${safeBody}</blockquote>`;
```

Change forward HTML to:

```ts
const fwdHtml = `<p><br></p><div data-flymail-quote="forward"><p>---------- 转发的邮件 ----------</p><p>发件人: ${safeFrom}</p><p>主题: ${safeSubject}</p><p>日期: ${safeDate}</p><hr/><div>${safeBody}</div></div>`;
```

Add a `mailQuote` node extension in `TiptapEditor.vue` that preserves the marker and original tag.

- [ ] **Step 4: Add a managed spacer paragraph attribute**

Extend `CustomParagraph` with:

```ts
signatureSpacer: {
  default: false,
  parseHTML: (element: HTMLElement) => element.hasAttribute('data-flymail-signature-spacer'),
  renderHTML: (attributes: Record<string, any>) => (
    attributes.signatureSpacer ? { 'data-flymail-signature-spacer': 'true' } : {}
  ),
},
```

The spacer is only considered reusable when it is an empty paragraph.

- [ ] **Step 5: Replace setManagedSignature with one transaction**

Implementation rules:

```ts
function setManagedSignature(signatureId: number | null, html = '', placement: 'start' | 'end' = 'end') {
  const currentEditor = editor.value;
  if (!currentEditor) return;

  let transaction = currentEditor.state.tr;
  let preservedInsertionPos: number | null = null;

  // Collect signatureBlock ranges and remember the first signature position.
  // Delete ranges in reverse and map the remembered position through transaction.mapping.
  // Never delete other node types.

  // Ensure first node is a paragraph; insert one at document start when needed.
  // Reuse or create one empty paragraph with signatureSpacer=true immediately before insertion.
  // For a first 'start' insertion, prefer the first mailQuote position; otherwise use the first body paragraph boundary.
  // For 'end', append after all existing user body nodes.

  if (signatureId !== null && html.trim()) {
    const signatureNode = currentEditor.schema.nodes.signatureBlock.create(
      { signatureId: String(signatureId) },
      currentEditor.schema.nodeFromJSON(parsedSignatureContent),
    );
    transaction = transaction.insert(insertionPos, signatureNode);
  }

  transaction = transaction.setSelection(TextSelection.create(transaction.doc, 1));
  currentEditor.view.dispatch(transaction.scrollIntoView());
  currentEditor.commands.focus('start');
}
```

Use Tiptap `generateJSON(html, currentEditor.extensionManager.extensions)` or a temporary parsed slice to convert signature HTML into valid block content; do not concatenate the whole editor HTML and call `setContent`.

When `signatureId === null`, delete only signature nodes, remove only empty paragraphs whose `signatureSpacer` attribute is true, and keep every user paragraph unchanged.

- [ ] **Step 6: Run focused tests and build**

Run: `cd frontend && node --test tests/compose-signature-layout.test.mjs tests/compose-signature-contract.test.mjs tests/compose-workspace.test.ts && npm run build`

Expected: focused tests PASS; build PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/TiptapEditor.vue frontend/src/composables/useReplyForward.ts frontend/tests/compose-signature-layout.test.mjs frontend/tests/compose-signature-contract.test.mjs frontend/tests/compose-workspace.test.ts
git commit -m "🐛 修复签名前正文消失与光标位置"
```

### Task 4: Browser regression for image sizing and compose preservation

**Files:**
- Modify only if a real browser failure identifies a product bug in Task 1-3 files.
- Do not commit browser scripts or screenshots; keep them under `/tmp`.

**Interfaces:**
- Validates the public behavior of Tasks 1-3.

- [ ] **Step 1: Run production preview with mocked API**

Run Vite preview through the existing `/tmp/flymail-playwright-venv` Playwright environment. Mock authentication, accounts, signatures, image upload and mail list APIs.

- [ ] **Step 2: Verify desktop image drag**

Browser steps:

1. Open signature management and create a signature.
2. Upload a valid PNG.
3. Click the inserted image.
4. Assert the resize handle and 25/50/75/100 toolbar are visible.
5. Drag the handle left.
6. Assert the image `width` attribute decreases and `signatureStore.draft.content_html` contains that width.
7. Save, reopen, and assert the same width is rendered.

- [ ] **Step 3: Verify mobile quick sizes**

Use viewport `390 × 844`, select the image, click `50%`, and assert width equals half the editor width within one pixel and no horizontal overflow exists.

- [ ] **Step 4: Verify compose cursor and text preservation**

Browser steps:

1. Open new compose with a default signature.
2. Assert the document begins with body paragraph, managed spacer, then signature.
3. Assert selection is in the first paragraph.
4. Type three separate paragraphs.
5. Switch signatures and switch sender account.
6. Assert all three paragraphs remain byte-for-byte and remain outside `.flymail-signature-block`.
7. Assert one empty spacer remains before the signature.
8. Select no signature and assert all three paragraphs remain.

- [ ] **Step 5: Verify reply and forward order**

For generated reply and forward drafts, assert DOM order is body paragraphs → managed spacer → signature → `[data-flymail-quote]` and selection remains at the first paragraph.

- [ ] **Step 6: Re-run affected automated tests after any browser-found fix**

Run: `cd frontend && npm test && npm run build`

Expected: all tests PASS and build PASS.

- [ ] **Step 7: Commit browser-found fixes if any**

```bash
git add frontend/src/components/TiptapEditor.vue frontend/src/utils/resizable-image-node-view.ts frontend/src/composables/useReplyForward.ts
git commit -m "🐛 修复签名缩放浏览器交互边界"
```

Skip this commit when no source changes are needed.

### Task 5: Documentation, version 0.0.37 and final delivery

**Files:**
- Modify: `README.md`
- Modify: `VERSION`
- Modify through `npm run sync-version`: `package.json`, `frontend/package.json`, `docker-compose.yml`
- Modify lock metadata if npm rewrites the root frontend package version: `frontend/package-lock.json`

**Interfaces:**
- Produces release `0.0.37` and local image `benxianyu/flymail:0.0.37`.

- [ ] **Step 1: Update README behavior**

Document:

- Uploaded signature images can be resized by drag or 25/50/75/100 shortcuts.
- Width is stored in signature HTML and preserved when sending.
- Applying a signature creates a top body paragraph and one separator line, then returns the caret to the top.
- Switching signatures or accounts does not remove text typed above the signature.
- Drafts remain unmodified.

- [ ] **Step 2: Set and synchronize version**

Set `VERSION` to `0.0.37`, then run:

```bash
npm run sync-version
```

Verify:

```bash
cat VERSION
node -e "console.log(require('./package.json').version)"
node -e "console.log(require('./frontend/package.json').version)"
```

Expected: all print `0.0.37`.

- [ ] **Step 3: Run complete code verification**

```bash
cd backend && python -m unittest discover -s tests -v
cd ../frontend && npm install && npm test && npm run build
cd ..
bash -n scripts/docker-entrypoint.sh
cp .env.example /tmp/flymail-compose.env
docker compose --env-file /tmp/flymail-compose.env config >/tmp/flymail-compose-config.yml
git diff --check
git status --short
git diff
```

Expected: backend and frontend have zero failures; build, shell, Compose and diff checks succeed.

- [ ] **Step 4: Build image**

Run: `docker build -t benxianyu/flymail:0.0.37 .`

Expected: build exits 0 and the image metadata contains no runtime passwords or session secrets.

- [ ] **Step 5: Validate an isolated temporary container**

Use a unique container name and a unique directory under `/Docker/flymail/`, never `/Docker/flymail/data`. Use a fixed free host port and a database password containing quotes, backslashes, `@`, `:`, `/` and `%`.

Verify:

1. Container becomes `healthy` and `/api/health` returns `0.0.37`.
2. MySQL is 8.0 and reports `/data/mysql/`.
3. `/data/flymail` and signature image directories exist.
4. Database test row survives restart.
5. Existing signature image upload/download still works.
6. Logs and image metadata contain no secret values.
7. `/data/mysql/error.log` records safe MySQL shutdown.
8. Temporary container and data are removed.

- [ ] **Step 6: Replace current production container with rollback protection**

Read current `flymail` image, bind mount, port, restart policy, network and data fingerprint without printing environment values. Stop and rename the old container, create `flymail` from `benxianyu/flymail:0.0.37` with identical runtime configuration, then verify health, port, `/Docker/flymail/data:/data`, MySQL version, data fingerprint and a second restart. Delete the stopped rollback container only after every check passes.

- [ ] **Step 7: Commit and push**

```bash
git status --short
git diff --check
git add README.md VERSION package.json frontend/package.json frontend/package-lock.json docker-compose.yml
git diff --staged
git commit -m "🚀 发布签名编辑体验 0.0.37"
git push origin main
```

Expected: local and `origin/main` SHA match and worktree is clean.

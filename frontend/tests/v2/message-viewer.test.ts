import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { Window } from 'happy-dom';

import { sanitizeMailHtml, safeLinkDomain } from '../../src/features/message-viewer/body-sanitizer.ts';
import { BodyRequestRegistry, bodyStateMessage } from '../../src/features/message-viewer/body-state.ts';
import { createImageViewerState } from '../../src/features/message-viewer/image-viewer-state.ts';
import { buildPrintableMailHtml } from '../../src/features/message-viewer/export-pdf.ts';

test('sanitizer removes active content dangerous urls and remote images by default', () => {
  const window = new Window();
  const dirty = `<style>body{display:none}</style><script>alert(1)</script><form action="/steal"><input></form>
    <a href="javascript:alert(1)" onclick="x()">bad</a><a href="https://example.com/path">safe</a>
    <img src="https://tracker.example/pixel.png" onerror="x()"><img src="cid:logo">`;
  const result = sanitizeMailHtml(dirty, { window, allowRemoteImages: false });
  assert.doesNotMatch(result.html, /script|form|onclick|onerror|javascript:|<style/i);
  assert.doesNotMatch(result.html, /tracker\.example/);
  assert.match(result.html, /data-remote-image-blocked/);
  assert.match(result.html, /cid:logo/);
  assert.deepEqual(result.blockedRemoteImages, ['https://tracker.example/pixel.png']);
  assert.equal(safeLinkDomain('https://example.com/path'), 'example.com');
});

test('body registry deduplicates queued requests and state copy is actionable', async () => {
  let calls = 0;
  const registry = new BodyRequestRegistry(async (messageId) => {
    calls += 1;
    return { message_id: messageId, state: 'queued', task_id: 'job-1' };
  });
  const first = registry.request('m1');
  const second = registry.request('m1');
  assert.equal(first, second);
  assert.equal((await first).state, 'queued');
  assert.equal(calls, 1);
  assert.match(bodyStateMessage('unavailable'), /重新连接|账号/);
  assert.match(bodyStateMessage('failed'), /重试/);
});

test('image viewer supports bounded zoom navigation drag and swipe', () => {
  const viewer = createImageViewerState(['a', 'b', 'c']);
  viewer.zoomBy(100);
  assert.equal(viewer.scale, 4);
  viewer.zoomBy(-100);
  assert.equal(viewer.scale, 1);
  viewer.next();
  viewer.zoomBy(1);
  viewer.dragBy(20, -10);
  assert.equal(viewer.current, 'b');
  assert.deepEqual(viewer.offset, { x: 20, y: -10 });
  viewer.swipe(-80, 20);
  assert.equal(viewer.current, 'c');
});

test('print output uses sanitized clone and removes controls without mutating source', () => {
  const window = new Window();
  const source = window.document.createElement('article');
  source.innerHTML = '<button>retry</button><p style="color:#111">Safe body</p><span data-remote-image-blocked>remote</span>';
  const original = source.innerHTML;
  const printable = buildPrintableMailHtml({ subject: 'Test', source, document: window.document });
  assert.doesNotMatch(printable, /button|data-remote-image-blocked/);
  assert.match(printable, /Safe body/);
  assert.equal(source.innerHTML, original);
});

test('viewer components expose body states safe attachments and isolated failures', async () => {
  const detail = await readFile(new URL('../../src/features/message-viewer/ThreadDetail.vue', import.meta.url), 'utf8');
  const body = await readFile(new URL('../../src/features/message-viewer/MessageBody.vue', import.meta.url), 'utf8');
  const attachments = await readFile(new URL('../../src/features/message-viewer/AttachmentList.vue', import.meta.url), 'utf8');
  assert.match(detail, /body_state/);
  assert.match(detail, /latestUnread/);
  assert.match(body, /data-mail-body/);
  assert.match(body, /allowRemoteImages/);
  assert.doesNotMatch(body, /v-html="[^s]/);
  assert.match(attachments, /application\/svg\+xml|text\/html/);
  assert.match(detail, /MessageTimelineItem/);
});

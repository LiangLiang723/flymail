import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import { createBootstrapController } from '../../src/app/bootstrap.ts';
import { createErrorBoundaryState } from '../../src/app/error-boundary.ts';
import { layoutForWidth } from '../../src/app/router.ts';

test('bootstrap executes exactly once and classifies unauthenticated state', async () => {
  let calls = 0;
  const controller = createBootstrapController(async () => {
    calls += 1;
    return {
      user: { id: 'user-1', username: 'user', role: 'user', enabled: true },
      permissions: [], accounts: [], preferences: {}, csrf_token: 'csrf', realtime_cursor: 0, version: '0.0.25',
    };
  });
  const [first, second] = await Promise.all([controller.load(), controller.load()]);
  assert.equal(calls, 1);
  assert.equal(first, second);
  assert.equal(controller.state.phase, 'authenticated');

  const anonymous = createBootstrapController(async () => {
    throw { status: 401, data: { error: { code: 'authentication_required', message: 'login' } } };
  });
  await anonymous.load();
  assert.equal(anonymous.state.phase, 'anonymous');
});

test('responsive layout contract maps mobile tablet and desktop exactly', () => {
  assert.equal(layoutForWidth(767), 'mobile');
  assert.equal(layoutForWidth(768), 'tablet');
  assert.equal(layoutForWidth(1199), 'tablet');
  assert.equal(layoutForWidth(1200), 'desktop');
});

test('error boundary preserves navigation state and retries', async () => {
  let retries = 0;
  const boundary = createErrorBoundaryState(async () => { retries += 1; });
  boundary.capture(new Error('chunk failed'));
  assert.equal(boundary.state.hasError, true);
  await boundary.retry();
  assert.equal(retries, 1);
  assert.equal(boundary.state.hasError, false);
});

test('app source mounts one selected layout and keeps heavy pages lazy', async () => {
  const app = await readFile(new URL('../../src/app/AppV2.vue', import.meta.url), 'utf8');
  const router = await readFile(new URL('../../src/app/router.ts', import.meta.url), 'utf8');
  assert.match(app, /<component\s+:is="activeLayout"/);
  assert.doesNotMatch(app, /<DesktopMailLayout|<TabletMailLayout|<MobileMailLayout/);
  for (const route of ['compose', 'search', 'settings', 'sync', 'admin', 'backup']) {
    assert.match(router, new RegExp(`path: '/${route}`));
  }
  assert.match(router, /component:\s*\(\)\s*=>\s*import/);
  assert.doesNotMatch(router, /Tiptap|NotificationDrawer/);
});

test('layouts expose desktop three regions tablet two and mobile stack semantics', async () => {
  const desktop = await readFile(new URL('../../src/layouts/DesktopMailLayout.vue', import.meta.url), 'utf8');
  const tablet = await readFile(new URL('../../src/layouts/TabletMailLayout.vue', import.meta.url), 'utf8');
  const mobile = await readFile(new URL('../../src/layouts/MobileMailLayout.vue', import.meta.url), 'utf8');
  assert.match(desktop, /data-region="navigation"/);
  assert.match(desktop, /data-region="thread-list"/);
  assert.match(desktop, /data-region="thread-detail"/);
  assert.match(tablet, /data-region="navigation"/);
  assert.match(tablet, /data-region="content"/);
  assert.match(mobile, /data-region="page-stack"/);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import {
  DEFAULT_BODY_QUOTA_BYTES,
  formatQuota,
  quotaDecreaseNeedsCleanup,
} from '../../src/features/settings/settings-state.ts';
import { canAccessAdminRoute, exactConfirmation } from '../../src/features/admin/admin-state.ts';
import { clearSecretAfter, restoreReviewItems } from '../../src/features/backup/backup-state.ts';

test('quota default is 5 GB, zero is unlimited and decrease distinguishes cleanup', () => {
  assert.equal(DEFAULT_BODY_QUOTA_BYTES, 5 * 1024 ** 3);
  assert.equal(formatQuota(0), '不限额');
  assert.equal(quotaDecreaseNeedsCleanup(10, 5), true);
  assert.equal(quotaDecreaseNeedsCleanup(5, 10), false);
});

test('admin route and destructive confirmation are exact', () => {
  assert.equal(canAccessAdminRoute('admin'), true);
  assert.equal(canAccessAdminRoute('user'), false);
  assert.equal(exactConfirmation('alice', 'alice'), true);
  assert.equal(exactConfirmation('alice', 'Alice'), false);
});

test('backup password is cleared after request and restore review blocks automatic execution', async () => {
  let password = 'secret-value';
  await clearSecretAfter(() => password, (value) => { password = value; }, async (secret) => {
    assert.equal(secret, 'secret-value');
    return { ok: true };
  });
  assert.equal(password, '');
  assert.deepEqual(restoreReviewItems({ pending_sends: 2, pending_remote_operations: 3 }), [
    { kind: 'pending_send', count: 2, state: 'review_required', automatic: false },
    { kind: 'remote_operation', count: 3, state: 'review_required', automatic: false },
  ]);
});

test('settings sync admin backup and about pages expose bounded safe behavior', async () => {
  const settings = await readFile(new URL('../../src/features/settings/SettingsPage.vue', import.meta.url), 'utf8');
  const sync = await readFile(new URL('../../src/features/sync-center/SyncCenterPage.vue', import.meta.url), 'utf8');
  const admin = await readFile(new URL('../../src/features/admin/AdminPage.vue', import.meta.url), 'utf8');
  const backup = await readFile(new URL('../../src/features/backup/BackupPage.vue', import.meta.url), 'utf8');
  const about = await readFile(new URL('../../src/features/about/AboutPage.vue', import.meta.url), 'utf8');
  assert.match(settings, /5 GB|不限额/);
  assert.match(settings, /cleanup_task_id|清理任务/);
  assert.match(sync, /summary|摘要/);
  assert.match(sync, /body|正文/);
  assert.match(sync, /index|索引/);
  assert.match(sync, /accounts.*refresh|refresh.*accounts/s);
  assert.match(admin, /reset-password/);
  assert.match(admin, /disable/);
  assert.doesNotMatch(admin, /minlength=["']12["']|length\s*<\s*12|至少\s*12/);
  assert.match(admin, /!resetPassword/);
  assert.match(backup, /不包含远端缓存|remote cache/i);
  assert.match(backup, /restore-rehearsal/);
  assert.match(backup, /review_required/);
  assert.doesNotMatch(backup, /minlength=["']12["']|length\s*<\s*12|至少\s*12/);
  assert.match(backup, /:disabled="!password"/);
  assert.doesNotMatch(backup, /localStorage|sessionStorage/);
  assert.match(about, /api\/v2\/version/);
  assert.match(about, /许可证|license/i);
  assert.doesNotMatch(about, /process\.env|\/data\/|filesystem|dependency/i);
});

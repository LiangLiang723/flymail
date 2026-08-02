import test from 'node:test';
import assert from 'node:assert/strict';
import { access, readFile } from 'node:fs/promises';

import { normalizeSquareCrop } from '../../src/features/account-customization/image-crop.ts';
import { validateOAuthCallback } from '../../src/features/accounts/oauth-state.ts';
import { mergeTypedRecipient } from '../../src/features/contacts/contact-state.ts';
import { configuredSecret } from '../../src/features/notifications/notification-state.ts';

test('square crop stays bounded and preserves source orientation metadata', () => {
  assert.deepEqual(normalizeSquareCrop({ x: -10, y: 20, size: 500, width: 300, height: 200, orientation: 6 }), {
    x: 0, y: 0, size: 200, width: 300, height: 200, orientation: 6,
  });
});

test('oauth callback rejects mismatched, expired and cancelled states', () => {
  assert.equal(validateOAuthCallback({ expectedState: 's1', returnedState: 's1', expiresAt: 2000, now: 1000, status: 'success' }).ok, true);
  assert.equal(validateOAuthCallback({ expectedState: 's1', returnedState: 's2', expiresAt: 2000, now: 1000, status: 'success' }).reason, 'state_mismatch');
  assert.equal(validateOAuthCallback({ expectedState: 's1', returnedState: 's1', expiresAt: 500, now: 1000, status: 'success' }).reason, 'expired');
  assert.equal(validateOAuthCallback({ expectedState: 's1', returnedState: 's1', expiresAt: 2000, now: 1000, status: 'cancelled' }).reason, 'cancelled');
});

test('contact autocomplete preserves exact typed address and secret fields show configured only', () => {
  assert.deepEqual(mergeTypedRecipient('Exact@Example.com', [{ address: 'other@example.com', display_name: 'Other' }]), [
    { address: 'Exact@Example.com', display_name: '' },
    { address: 'other@example.com', display_name: 'Other' },
  ]);
  assert.equal(configuredSecret(true, 'token-value'), '已配置');
  assert.equal(configuredSecret(false, 'token-value'), '');
});

test('personal account contact notification components implement safe contracts', async () => {
  const profile = await readFile(new URL('../../src/features/profile/ProfilePage.vue', import.meta.url), 'utf8');
  const wizard = await readFile(new URL('../../src/features/accounts/AccountSetupWizard.vue', import.meta.url), 'utf8');
  const accountPage = await readFile(new URL('../../src/features/accounts/AccountsPage.vue', import.meta.url), 'utf8');
  const proxy = await readFile(new URL('../../src/features/accounts/AccountProxyForm.vue', import.meta.url), 'utf8');
  const oauth = await readFile(new URL('../../src/features/accounts/OAuthCallbackPage.vue', import.meta.url), 'utf8');
  const contacts = await readFile(new URL('../../src/features/contacts/ContactsPage.vue', import.meta.url), 'utf8');
  const autocomplete = await readFile(new URL('../../src/features/contacts/ContactAutocomplete.vue', import.meta.url), 'utf8');
  const notifications = await readFile(new URL('../../src/features/notifications/NotificationSettingsPage.vue', import.meta.url), 'utf8');
  const center = await readFile(new URL('../../src/features/notifications/NotificationCenter.vue', import.meta.url), 'utf8');
  const icon = await readFile(new URL('../../src/features/account-customization/AccountIconEditor.vue', import.meta.url), 'utf8');
  assert.match(profile, /profile\/avatar/);
  assert.match(profile, /nickname/);
  assert.match(profile, /method:\s*'PATCH'.*path:\s*'\/api\/v2\/profile'/s);
  assert.doesNotMatch(profile, /method:\s*'PUT'.*path:\s*'\/api\/v2\/profile'/s);
  assert.match(wizard, /generic|IMAP|SMTP/);
  assert.match(wizard, /oauth/i);
  assert.match(wizard, /security:\s*'tls'/);
  assert.doesNotMatch(wizard, /tls_mode/);
  assert.doesNotMatch(wizard, /localStorage|sessionStorage/);
  assert.match(accountPage, /\/credentials/);
  assert.match(accountPage, /\/identities/);
  assert.match(accountPage, /confirm_email/);
  assert.match(proxy, /账号流量/);
  assert.match(proxy, /内部 FlyMail/);
  assert.doesNotMatch(proxy, /username:\s*form\.username\s*\|\|\s*null/);
  assert.doesNotMatch(proxy, /password:\s*form\.password\s*\|\|\s*null/);
  assert.doesNotMatch(proxy, /value=.*password/i);
  assert.match(oauth, /validateOAuthCallback/);
  assert.match(oauth, /method:\s*'GET'/);
  assert.match(oauth, /query:\s*\{\s*state:\s*returnedState,\s*code\s*\}/);
  assert.match(autocomplete, /ArrowDown/);
  assert.match(contacts, /method:\s*'PATCH'/);
  assert.match(notifications, /Bark|Telegram|企业微信|DingTalk|Feishu|Webhook/);
  assert.match(notifications, /flymail-imgbed/);
  assert.match(notifications, /文本回退/);
  assert.match(notifications, /\/api\/v2\/notification-publishers/);
  assert.doesNotMatch(notifications, /\/api\/v2\/image-hosts/);
  assert.match(notifications, /channel_key/);
  assert.match(notifications, /display_name/);
  assert.match(notifications, /event_type/);
  assert.match(notifications, /channel_id/);
  assert.doesNotMatch(notifications, /localStorage|sessionStorage/);
  assert.match(center, /unread|未读/);
  assert.match(icon, /provider|preset|upload/);
  assert.doesNotMatch(icon, /builtin|value="work"/);
});

test('PWA caches static assets only and never mail or API data', async () => {
  const manifest = JSON.parse(await readFile(new URL('../../public/manifest.webmanifest', import.meta.url), 'utf8'));
  const worker = await readFile(new URL('../../public/flymail-sw.js', import.meta.url), 'utf8');
  const register = await readFile(new URL('../../src/features/pwa/register.ts', import.meta.url), 'utf8');
  const html = await readFile(new URL('../../index.html', import.meta.url), 'utf8');
  assert.equal(manifest.display, 'standalone');
  assert.ok(Array.isArray(manifest.icons) && manifest.icons.length >= 2);
  for (const icon of manifest.icons) {
    assert.match(icon.src, /^\/icons\/[A-Za-z0-9._-]+\.png$/);
    await access(new URL(`../../public${icon.src}`, import.meta.url));
  }
  const htmlIcon = html.match(/rel="icon"[^>]+href="([^"]+)"/)?.[1] ?? '';
  assert.match(htmlIcon, /^\/icons\/[A-Za-z0-9._-]+\.png$/);
  await access(new URL(`../../public${htmlIcon}`, import.meta.url));
  for (const workerIcon of worker.matchAll(/['"](\/icons\/[A-Za-z0-9._-]+\.png)['"]/g)) {
    await access(new URL(`../../public${workerIcon[1]}`, import.meta.url));
  }
  assert.match(worker, /url\.origin !== self\.location\.origin/);
  assert.match(worker, /url\.pathname\.startsWith\('\/api\/'\)/);
  assert.match(worker, /request\.method !== 'GET'/);
  assert.match(worker, /\/body|attachments|backups|upload/);
  assert.match(worker, /networkFirstNavigation/);
  assert.doesNotMatch(worker, /indexedDB|localStorage|mail-body|attachment-data/);
  assert.match(register, /import\.meta\.env\.PROD/);
});

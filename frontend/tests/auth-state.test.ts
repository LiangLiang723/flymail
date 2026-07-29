import test from 'node:test';
import assert from 'node:assert/strict';

import {
  classifyAuthError,
  getLoginErrorMessage,
  normalizeApiError,
} from '../src/utils/auth-state.ts';

test('auth bootstrap treats only credential failures as anonymous', () => {
  assert.equal(classifyAuthError({ status: 401 }), 'anonymous');
  assert.equal(classifyAuthError({ status: 403 }), 'anonymous');
  assert.equal(classifyAuthError({ status: 500 }), 'error');
  assert.equal(classifyAuthError({ status: 0, network: true }), 'error');
});

test('login errors use safe and actionable Chinese messages', () => {
  assert.equal(getLoginErrorMessage({ status: 401 }), '用户名或密码错误');
  assert.equal(getLoginErrorMessage({ status: 403 }), '此账号已被禁用，请联系管理员');
  assert.equal(getLoginErrorMessage({ network: true }), '暂时无法连接 FlyMail，请稍后重试');
  assert.equal(getLoginErrorMessage({ status: 500, detail: '服务暂时不可用' }), '服务暂时不可用');
});

test('normalized errors preserve backend compatibility fields', () => {
  const normalized = normalizeApiError({
    response: {
      status: 409,
      data: { error: '联系人已存在', status_code: 409 },
    },
  });

  assert.equal(normalized.error, '联系人已存在');
  assert.equal(normalized.status_code, 409);
  assert.equal(normalized.message, '联系人已存在');
});

test('axios-like errors retain status and distinguish network failures', () => {
  assert.deepEqual(
    normalizeApiError({ response: { status: 401, data: { detail: '用户名或密码错误' } } }),
    { status: 401, detail: '用户名或密码错误', message: '用户名或密码错误', network: false },
  );
  assert.deepEqual(
    normalizeApiError({ code: 'ECONNABORTED', message: 'timeout of 30000ms exceeded' }),
    { status: 0, detail: '', message: 'timeout of 30000ms exceeded', network: true, code: 'ECONNABORTED' },
  );
});

<template>
  <main class="login-page">
    <section class="login-card" aria-labelledby="login-title">
      <div class="login-brand">
        <img src="/icon.png" alt="" class="brand-logo" />
        <div>
          <h1 id="login-title">欢迎回来</h1>
          <p>登录 FlyMail 继续处理邮件</p>
        </div>
      </div>

      <div v-if="errorMessage" class="login-alert" role="alert">
        <span class="login-alert-icon" aria-hidden="true">!</span>
        <span>{{ errorMessage }}</span>
      </div>

      <form class="login-form" @submit.prevent="submit">
        <label class="field" :class="{ 'has-error': Boolean(errorMessage) }">
          <span>用户名</span>
          <input
            v-model.trim="username"
            autocomplete="username"
            autofocus
            :aria-invalid="Boolean(errorMessage)"
            @input="clearCredentialError"
          />
        </label>
        <label class="field" :class="{ 'has-error': Boolean(errorMessage) }">
          <span>密码</span>
          <input
            v-model="password"
            type="password"
            autocomplete="current-password"
            :aria-invalid="Boolean(errorMessage)"
            @input="clearCredentialError"
          />
        </label>
        <button class="btn btn-primary login-submit" :disabled="loading || !username || !password">
          <span v-if="loading" class="login-spinner" aria-hidden="true"></span>
          <span>{{ loading ? '登录中…' : '登录' }}</span>
        </button>
      </form>
    </section>
  </main>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import api from '../utils/api';
import { getLoginErrorMessage, type ApiError } from '../utils/auth-state';

const emit = defineEmits<{
  success: []
}>();

const username = ref('');
const password = ref('');
const loading = ref(false);
const errorMessage = ref('');

function clearCredentialError() {
  if (errorMessage.value) errorMessage.value = '';
}

async function submit() {
  if (loading.value || !username.value || !password.value) return;
  loading.value = true;
  errorMessage.value = '';
  try {
    await api.post('/auth/login', {
      username: username.value,
      password: password.value,
    });
    emit('success');
  } catch (error) {
    errorMessage.value = getLoginErrorMessage(error as ApiError);
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100dvh;
  display: grid;
  place-items: center;
  padding: 24px;
  background:
    radial-gradient(circle at 20% 15%, var(--color-accent-lighter), transparent 34%),
    var(--bg-secondary);
}

.login-card {
  width: min(420px, 100%);
  padding: 34px;
  border: 1px solid var(--border-color);
  border-radius: 20px;
  background: var(--bg-card);
  box-shadow: var(--shadow-xl);
}

.login-brand {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 26px;
}

.brand-logo {
  width: 56px;
  height: 56px;
  flex: 0 0 auto;
}

.login-brand h1 {
  margin: 0;
  font-size: 26px;
  line-height: 1.12;
  letter-spacing: -0.025em;
}

.login-brand p {
  margin: 6px 0 0;
  color: var(--text-secondary);
  font-size: 13px;
}

.login-alert {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 18px;
  padding: 12px 14px;
  border: 1px solid color-mix(in srgb, var(--color-danger) 28%, transparent);
  border-radius: 12px;
  background: var(--color-danger-light);
  color: var(--color-danger);
  font-size: 13px;
  line-height: 1.45;
}

.login-alert-icon {
  width: 19px;
  height: 19px;
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  border: 1.5px solid currentColor;
  border-radius: 50%;
  font-size: 11px;
  font-weight: 700;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.field span {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.field input {
  width: 100%;
  height: 44px;
  border: 1px solid var(--border-color-strong);
  border-radius: 12px;
  padding: 0 13px;
  background: var(--bg-primary);
  color: var(--text-primary);
  font: inherit;
  font-size: 14px;
  outline: none;
  transition: border-color 140ms ease, box-shadow 140ms ease, background 140ms ease;
}

.field input:focus-visible {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 3px var(--color-accent-light);
}

.field.has-error input {
  border-color: color-mix(in srgb, var(--color-danger) 62%, var(--border-color-strong));
}

.login-submit {
  min-height: 44px;
  margin-top: 2px;
}

.login-spinner {
  width: 16px;
  height: 16px;
  border: 2px solid color-mix(in srgb, currentColor 35%, transparent);
  border-top-color: currentColor;
  border-radius: 50%;
  animation: login-spin 0.7s linear infinite;
}

@keyframes login-spin {
  to { transform: rotate(360deg); }
}

@media (max-width: 520px) {
  .login-page { padding: 16px; }
  .login-card { padding: 26px 22px; }
}

@media (prefers-reduced-motion: reduce) {
  .login-spinner { animation: none; }
}
</style>

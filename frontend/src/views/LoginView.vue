<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-brand">
        <img src="/icon.png" alt="FlyMail" class="brand-logo" />
        <div>
          <h1>FlyMail</h1>
          <p>多用户邮件系统</p>
        </div>
      </div>

      <form class="login-form" @submit.prevent="submit">
        <label class="field">
          <span>用户名</span>
          <input v-model="username" autocomplete="username" />
        </label>
        <label class="field">
          <span>密码</span>
          <input v-model="password" type="password" autocomplete="current-password" />
        </label>
        <button class="btn btn-primary" :disabled="loading">
          {{ loading ? '登录中...' : '登录' }}
        </button>
      </form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import api from '../utils/api';

const emit = defineEmits<{
  success: []
}>();

const username = ref('');
const password = ref('');
const loading = ref(false);

async function submit() {
  loading.value = true;
  try {
    await api.post('/auth/login', {
      username: username.value,
      password: password.value,
    });
    emit('success');
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-secondary);
  padding: 24px;
}

.login-card {
  width: min(420px, 100%);
  background: var(--bg-card);
  border-radius: 16px;
  padding: 32px;
  box-shadow: var(--shadow-xl);
}

.login-brand {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 28px;
}

.brand-logo {
  width: 56px;
  height: 56px;
}

.login-brand h1 {
  margin: 0;
  font-size: 28px;
}

.login-brand p {
  margin: 4px 0 0;
  color: var(--text-secondary);
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
  font-size: 14px;
  color: var(--text-primary);
}

.field input {
  height: 44px;
  border: 1px solid var(--border-color-strong);
  border-radius: 10px;
  padding: 0 12px;
  background: var(--bg-primary);
  color: var(--text-primary);
  font-size: 14px;
}

.btn {
  height: 44px;
  border: none;
  border-radius: 10px;
  cursor: pointer;
}

.btn-primary {
  background: var(--color-accent);
  color: var(--text-on-accent);
}
</style>

<template>
  <main class="login-page">
    <UiCard class="login-card" variant="raised" padding="lg" aria-labelledby="login-title">
      <div class="login-brand">
        <img src="/icon.png" alt="" class="brand-logo" />
        <div>
          <h1 id="login-title">欢迎回来</h1>
          <p>登录 FlyMail 继续处理邮件</p>
        </div>
      </div>

      <UiAlert v-if="errorMessage" tone="danger" role="alert">{{ errorMessage }}</UiAlert>

      <form class="login-form" @submit.prevent="submit">
        <UiField label="用户名" for-id="login-username">
          <input
            id="login-username"
            v-model.trim="username"
            class="ui-input login-input"
            autocomplete="username"
            autofocus
            :aria-invalid="Boolean(errorMessage)"
            @input="clearCredentialError"
          />
        </UiField>
        <UiField label="密码" for-id="login-password">
          <input
            id="login-password"
            v-model="password"
            class="ui-input login-input"
            type="password"
            autocomplete="current-password"
            :aria-invalid="Boolean(errorMessage)"
            @input="clearCredentialError"
          />
        </UiField>
        <UiButton
          class="login-submit"
          variant="primary"
          size="lg"
          type="submit"
          :loading="loading"
          :disabled="!username || !password"
        >
          {{ loading ? '登录中…' : '登录' }}
        </UiButton>
      </form>
    </UiCard>
  </main>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import UiAlert from '../components/ui/UiAlert.vue';
import UiButton from '../components/ui/UiButton.vue';
import UiCard from '../components/ui/UiCard.vue';
import UiField from '../components/ui/UiField.vue';
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
  padding: var(--ui-space-6);
  background:
    radial-gradient(circle at 50% 0%, var(--color-accent-lighter), transparent 38%),
    var(--ui-canvas);
}

.login-card {
  width: min(400px, 100%);
  border-radius: var(--ui-radius-lg);
}

.login-brand {
  display: flex;
  align-items: center;
  gap: var(--ui-space-3);
  margin-bottom: var(--ui-space-5);
}

.brand-logo {
  width: 48px;
  height: 48px;
  flex: 0 0 auto;
}

.login-brand h1 {
  margin: 0;
  color: var(--ui-text-1);
  font-size: 22px;
  line-height: 1.12;
  letter-spacing: -0.025em;
}

.login-brand p {
  margin: var(--ui-space-1) 0 0;
  color: var(--ui-text-2);
  font-size: var(--ui-text-sm);
}

.login-form {
  display: grid;
  gap: var(--ui-space-4);
  margin-top: var(--ui-space-4);
}

.login-input {
  min-height: var(--touch-target);
}

.login-submit {
  width: 100%;
  min-height: var(--touch-target);
}

@media (max-width: 520px) {
  .login-page {
    padding: var(--ui-space-4);
  }
}
</style>

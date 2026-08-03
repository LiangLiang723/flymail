<script setup lang="ts">
import { ref } from 'vue';
import { useRouter } from 'vue-router';

import UiAlert from '../../components/ui/UiAlert.vue';
import UiButton from '../../components/ui/UiButton.vue';
import UiCard from '../../components/ui/UiCard.vue';
import UiField from '../../components/ui/UiField.vue';
import { useAuthState } from './auth-state.ts';

const username = ref('');
const password = ref('');
const auth = useAuthState();
const router = useRouter();

async function submit() {
  if (!username.value || !password.value || auth.state.submitting) return;
  const success = await auth.login(username.value, password.value);
  password.value = '';
  if (success) await router.replace('/mail/inbox');
}
</script>

<template>
  <main class="v2-login login-page">
    <UiCard class="v2-login__card login-card" variant="raised" padding="lg" aria-labelledby="login-title">
      <div class="login-brand">
        <img src="/icon.png" alt="" class="brand-logo" />
        <div>
          <h1 id="login-title">欢迎回来</h1>
          <p>登录 FlyMail 继续处理邮件</p>
        </div>
      </div>

      <UiAlert v-if="auth.state.error" tone="danger" role="alert">
        {{ auth.state.error.message }}
      </UiAlert>

      <form class="login-form" @submit.prevent="submit">
        <UiField label="用户名" for-id="login-username">
          <input
            id="login-username"
            v-model.trim="username"
            class="ui-input login-input"
            name="username"
            autocomplete="username"
            autofocus
            required
            :aria-invalid="Boolean(auth.state.error)"
          />
        </UiField>
        <UiField label="密码" for-id="login-password">
          <input
            id="login-password"
            v-model="password"
            class="ui-input login-input"
            name="password"
            type="password"
            autocomplete="current-password"
            required
            :aria-invalid="Boolean(auth.state.error)"
          />
        </UiField>
        <UiButton
          class="login-submit"
          variant="primary"
          size="lg"
          type="submit"
          :loading="auth.state.submitting"
          :disabled="!username || !password"
        >
          {{ auth.state.submitting ? '登录中…' : '登录' }}
        </UiButton>
      </form>
    </UiCard>
  </main>
</template>

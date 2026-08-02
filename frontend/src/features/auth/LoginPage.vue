<script setup lang="ts">
import { ref } from 'vue';
import { useRouter } from 'vue-router';

import { useAuthState } from './auth-state.ts';

const username = ref('');
const password = ref('');
const auth = useAuthState();
const router = useRouter();

async function submit() {
  const success = await auth.login(username.value, password.value);
  password.value = '';
  if (success) await router.replace('/mail/inbox');
}
</script>

<template>
  <main class="v2-login">
    <form class="v2-login__card" aria-labelledby="login-title" @submit.prevent="submit">
      <p class="v2-eyebrow">FlyMail V2</p>
      <h1 id="login-title">登录邮箱工作台</h1>
      <label>
        <span>用户名</span>
        <input v-model.trim="username" name="username" autocomplete="username" required />
      </label>
      <label>
        <span>密码</span>
        <input v-model="password" name="password" type="password" autocomplete="current-password" required />
      </label>
      <p v-if="auth.state.error" class="v2-error" role="alert">{{ auth.state.error.message }}</p>
      <button type="submit" :disabled="auth.state.submitting">
        {{ auth.state.submitting ? '正在登录…' : '登录' }}
      </button>
    </form>
  </main>
</template>

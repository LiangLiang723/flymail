<script setup lang="ts">
import { reactive } from 'vue';

import { apiClient } from '../../shared/api/client.ts';
import { normalizeApiError } from '../../shared/api/errors.ts';

const props = defineProps<{ address: string; displayName?: string }>();
const emit = defineEmits<{ created: []; close: [] }>();
const form = reactive({
  display_name: props.displayName || '',
  primary_email: props.address,
  error: '',
  saving: false,
});

async function save() {
  form.saving = true;
  form.error = '';
  try {
    await apiClient.request({
      method: 'POST',
      path: '/api/v2/contacts',
      body: {
        display_name: form.display_name.trim() || form.primary_email,
        primary_email: form.primary_email.trim(),
        emails: [form.primary_email.trim()],
      },
    });
    emit('created');
  } catch (value: unknown) {
    form.error = normalizeApiError(value).message;
  } finally {
    form.saving = false;
  }
}
</script>

<template>
  <form class="v2-quick-contact" role="dialog" aria-modal="true" aria-labelledby="quick-contact-title" @submit.prevent="save">
    <h2 id="quick-contact-title">添加联系人</h2>
    <label>姓名<input v-model.trim="form.display_name" /></label>
    <label>邮箱<input v-model.trim="form.primary_email" type="email" required /></label>
    <p v-if="form.error" class="v2-error" role="alert">{{ form.error }}</p>
    <button type="submit" :disabled="form.saving">{{ form.saving ? '保存中…' : '保存联系人' }}</button>
    <button type="button" @click="emit('close')">取消</button>
  </form>
</template>

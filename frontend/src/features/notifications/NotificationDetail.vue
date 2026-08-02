<script setup lang="ts">
import type { NotificationSummary } from '../../shared/api/generated.ts';

defineProps<{ notification: NotificationSummary }>();
const emit = defineEmits<{ close: [] }>();
</script>

<template>
  <section class="v2-notification-detail" role="dialog" aria-modal="true" aria-labelledby="notification-detail-title">
    <h2 id="notification-detail-title">{{ notification.title }}</h2>
    <p>{{ notification.summary }}</p>
    <time>{{ new Date(notification.created_at * 1000).toLocaleString() }}</time>
    <router-link v-if="notification.action_path" :to="notification.action_path">打开相关页面</router-link>
    <button type="button" @click="emit('close')">关闭</button>
  </section>
</template>

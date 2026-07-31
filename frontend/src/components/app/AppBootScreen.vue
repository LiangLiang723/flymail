<template>
  <main class="app-boot" :class="{ 'is-error': Boolean(message) }" aria-live="polite">
    <UiCard class="app-boot-card" variant="raised" padding="lg">
      <img src="/icon.png" alt="" class="app-boot-logo" />
      <div class="app-boot-copy">
        <h1>FlyMail</h1>
        <p>{{ message || '正在恢复你的工作区…' }}</p>
      </div>
      <UiSpinner v-if="!message" :size="20" />
      <UiButton v-else variant="primary" @click="$emit('retry')">重新连接</UiButton>
    </UiCard>
  </main>
</template>

<script setup lang="ts">
import UiButton from '../ui/UiButton.vue';
import UiCard from '../ui/UiCard.vue';
import UiSpinner from '../ui/UiSpinner.vue';

defineProps<{
  message?: string;
}>();

defineEmits<{
  retry: [];
}>();
</script>

<style scoped>
.app-boot {
  min-height: 100dvh;
  display: grid;
  place-items: center;
  padding: var(--ui-space-6);
  background: var(--ui-canvas);
  color: var(--ui-text-1);
}

.app-boot-card {
  width: min(360px, 100%);
  text-align: center;
}

.app-boot-card :deep(.ui-card__body) {
  display: grid;
  justify-items: center;
  gap: var(--ui-space-4);
}

.app-boot-logo {
  width: 56px;
  height: 56px;
  filter: drop-shadow(0 10px 22px color-mix(in srgb, var(--ui-text-1) 12%, transparent));
}

.app-boot-copy h1 {
  margin: 0;
  color: var(--ui-text-1);
  font-size: 25px;
  line-height: 1.1;
  letter-spacing: -0.025em;
}

.app-boot-copy p {
  margin: var(--ui-space-2) 0 0;
  color: var(--ui-text-2);
  font-size: var(--ui-text-sm);
}
</style>

<template>
  <main class="app-boot" :class="{ 'is-error': Boolean(message) }" aria-live="polite">
    <section class="app-boot-card">
      <img src="/icon.png" alt="" class="app-boot-logo" />
      <div class="app-boot-copy">
        <h1>FlyMail</h1>
        <p>{{ message || '正在恢复你的工作区…' }}</p>
      </div>
      <span v-if="!message" class="app-boot-spinner" aria-hidden="true"></span>
      <button v-else type="button" class="app-boot-retry" @click="$emit('retry')">重新连接</button>
    </section>
  </main>
</template>

<script setup lang="ts">
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
  padding: 24px;
  background: var(--bg-secondary);
  color: var(--text-primary);
}

.app-boot-card {
  width: min(360px, 100%);
  display: grid;
  justify-items: center;
  gap: 18px;
  padding: 32px;
  text-align: center;
}

.app-boot-logo {
  width: 56px;
  height: 56px;
  filter: drop-shadow(0 10px 22px color-mix(in srgb, var(--ui-text-1) 12%, transparent));
}

.app-boot-copy h1 {
  margin: 0;
  font-size: 25px;
  line-height: 1.1;
  letter-spacing: -0.025em;
}

.app-boot-copy p {
  margin: 8px 0 0;
  color: var(--text-secondary);
  font-size: 13px;
}

.app-boot-spinner {
  width: 20px;
  height: 20px;
  border: 2px solid var(--border-color-strong);
  border-top-color: var(--color-accent);
  border-radius: 50%;
  animation: app-boot-spin 0.75s linear infinite;
}

.app-boot-retry {
  min-height: 40px;
  padding: 0 18px;
  border: 0;
  border-radius: 12px;
  background: var(--color-accent);
  color: var(--text-on-accent);
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}

@keyframes app-boot-spin {
  to { transform: rotate(360deg); }
}

@media (prefers-reduced-motion: reduce) {
  .app-boot-spinner { animation: none; }
}
</style>

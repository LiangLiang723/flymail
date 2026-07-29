<template>
  <AppBootScreen
    v-if="state === 'booting' || state === 'error'"
    :message="state === 'error' ? errorMessage : ''"
    @retry="$emit('retry')"
  />
  <slot v-else-if="state === 'authenticated'" />
  <slot v-else name="anonymous" />
</template>

<script setup lang="ts">
import type { AuthState } from '../../utils/auth-state';
import AppBootScreen from './AppBootScreen.vue';

defineProps<{
  state: AuthState;
  errorMessage?: string;
}>();

defineEmits<{
  retry: [];
}>();
</script>

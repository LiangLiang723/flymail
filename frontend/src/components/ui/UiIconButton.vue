<template>
  <button
    class="ui-icon-button"
    :class="[`ui-icon-button--${size}`, { 'is-loading': loading }]"
    type="button"
    :aria-label="label"
    :title="title || label"
    :disabled="disabled || loading"
  >
    <UiSpinner v-if="loading" :size="size === 'sm' ? 14 : 16" />
    <slot v-else />
  </button>
</template>

<script setup lang="ts">
import UiSpinner from './UiSpinner.vue';

withDefaults(defineProps<{
  label: string;
  title?: string;
  size?: 'sm' | 'md' | 'lg';
  loading?: boolean;
  disabled?: boolean;
}>(), {
  title: '',
  size: 'md',
  loading: false,
  disabled: false,
});
</script>

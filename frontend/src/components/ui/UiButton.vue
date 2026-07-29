<template>
  <button
    class="ui-button"
    :class="[`ui-button--${variant}`, `ui-button--${size}`, { 'is-loading': loading }]"
    :type="type"
    :disabled="disabled || loading"
  >
    <UiSpinner v-if="loading" />
    <span v-else-if="$slots.leading" class="ui-button__icon"><slot name="leading" /></span>
    <span class="ui-button__label"><slot /></span>
    <span v-if="$slots.trailing && !loading" class="ui-button__icon"><slot name="trailing" /></span>
  </button>
</template>

<script setup lang="ts">
import UiSpinner from './UiSpinner.vue';

withDefaults(defineProps<{
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  size?: 'sm' | 'md' | 'lg';
  loading?: boolean;
  disabled?: boolean;
  type?: 'button' | 'submit' | 'reset';
}>(), {
  variant: 'secondary',
  size: 'md',
  loading: false,
  disabled: false,
  type: 'button',
});
</script>

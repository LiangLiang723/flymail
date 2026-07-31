<template>
  <label class="ui-field" :for="forId">
    <span v-if="label" class="ui-field__label">
      {{ label }}<span v-if="required" aria-hidden="true"> *</span>
    </span>
    <slot />
    <span v-if="error || hint" class="ui-field__message" :class="{ 'is-error': Boolean(error) }">
      {{ error || hint }}
    </span>
  </label>
</template>

<script setup lang="ts">
defineProps<{
  label?: string;
  forId?: string;
  hint?: string;
  error?: string;
  required?: boolean;
}>();
</script>

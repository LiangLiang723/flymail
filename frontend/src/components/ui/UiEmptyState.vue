<template>
  <div class="ui-empty-state" role="status">
    <div v-if="icon || $slots.icon" class="ui-empty-state__icon" aria-hidden="true">
      <slot name="icon">{{ icon }}</slot>
    </div>
    <div class="ui-empty-state__copy">
      <h2>{{ title }}</h2>
      <p v-if="description">{{ description }}</p>
    </div>
    <div v-if="$slots.default" class="ui-empty-state__actions">
      <slot />
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  title: string;
  description?: string;
  icon?: string;
}>();
</script>

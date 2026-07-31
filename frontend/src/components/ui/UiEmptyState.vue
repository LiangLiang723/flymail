<template>
  <div
    class="ui-empty-state"
    :class="{ 'ui-empty-state--compact': compact, 'ui-empty-state--panel': panel }"
    role="status"
  >
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
withDefaults(defineProps<{
  title: string;
  description?: string;
  icon?: string;
  compact?: boolean;
  panel?: boolean;
}>(), {
  compact: false,
  panel: false,
});
</script>

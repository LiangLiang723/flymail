<template>
  <div
    class="ui-loading-state"
    :class="{ 'ui-loading-state--compact': compact, 'ui-loading-state--panel': panel }"
    role="status"
    aria-live="polite"
  >
    <span class="ui-loading-state__spinner" aria-hidden="true"></span>
    <span>{{ label }}</span>
  </div>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  label?: string;
  compact?: boolean;
  panel?: boolean;
}>(), {
  label: '加载中…',
  compact: false,
  panel: false,
});
</script>

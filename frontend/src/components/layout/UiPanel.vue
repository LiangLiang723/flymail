<template>
  <section class="ui-panel" :class="`ui-panel--${variant}`">
    <slot />
  </section>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  variant?: 'plain' | 'subtle' | 'raised';
}>(), {
  variant: 'plain',
});
</script>

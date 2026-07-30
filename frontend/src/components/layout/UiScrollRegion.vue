<template>
  <div class="ui-scroll-region" :class="axisClass">
    <slot />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';

const props = withDefaults(defineProps<{
  axis?: 'y' | 'x' | 'both';
}>(), {
  axis: 'y',
});

const axisClass = computed(() => `ui-scroll-region--${props.axis}`);
</script>

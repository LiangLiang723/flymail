<template>
  <span class="ui-spinner" :style="{ width: `${size}px`, height: `${size}px` }" aria-hidden="true"></span>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  size?: number;
}>(), {
  size: 16,
});
</script>

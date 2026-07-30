<template>
  <section class="page-frame" :class="`page-frame--${template}`">
    <div v-if="$slots.header" class="page-frame__header">
      <slot name="header" />
    </div>
    <div v-if="$slots.toolbar" class="page-frame__toolbar">
      <slot name="toolbar" />
    </div>
    <div class="page-frame__body">
      <slot />
    </div>
  </section>
</template>

<script setup lang="ts">
type PageTemplate = 'workspace' | 'management' | 'split' | 'document';

withDefaults(defineProps<{
  template?: PageTemplate;
}>(), {
  template: 'management',
});
</script>

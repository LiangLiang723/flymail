<template>
  <section
    class="page-frame"
    :class="[
      `page-frame--${template}`,
      `page-frame--width-${resolvedWidth}`,
      { 'page-frame--has-header': Boolean($slots.header), 'page-frame--has-toolbar': Boolean($slots.toolbar) },
    ]"
  >
    <div
      class="page-frame__shell"
      :class="{
        'page-frame--has-header': Boolean($slots.header),
        'page-frame--has-toolbar': Boolean($slots.toolbar),
      }"
    >
      <div v-if="$slots.header" class="page-frame__header">
        <slot name="header" />
      </div>
      <div v-if="$slots.toolbar" class="page-frame__toolbar">
        <slot name="toolbar" />
      </div>
      <div class="page-frame__body">
        <slot />
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue';

type PageTemplate = 'workspace' | 'management' | 'split' | 'document';
type PageWidth = 'fluid' | 'form' | 'reading';

const props = withDefaults(defineProps<{
  template?: PageTemplate;
  width?: PageWidth;
}>(), {
  template: 'management',
});

const resolvedWidth = computed<PageWidth>(() => (
  props.width || (props.template === 'document' ? 'form' : 'fluid')
));
</script>

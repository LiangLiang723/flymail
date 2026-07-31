<template>
  <section
    class="ui-card"
    :class="[
      `ui-card--${variant}`,
      `ui-card--padding-${padding}`,
      { 'ui-card--interactive': interactive },
    ]"
  >
    <header v-if="$slots.header" class="ui-card__header"><slot name="header" /></header>
    <div class="ui-card__body"><slot /></div>
    <footer v-if="$slots.footer" class="ui-card__footer"><slot name="footer" /></footer>
  </section>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  variant?: 'default' | 'subtle' | 'raised';
  padding?: 'none' | 'sm' | 'md' | 'lg';
  interactive?: boolean;
}>(), {
  variant: 'default',
  padding: 'md',
  interactive: false,
});
</script>

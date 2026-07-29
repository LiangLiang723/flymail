<template>
  <section class="ui-card" :class="{ 'ui-card--interactive': interactive }">
    <header v-if="$slots.header" class="ui-card__header"><slot name="header" /></header>
    <div class="ui-card__body"><slot /></div>
    <footer v-if="$slots.footer" class="ui-card__footer"><slot name="footer" /></footer>
  </section>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  interactive?: boolean;
}>(), {
  interactive: false,
});
</script>

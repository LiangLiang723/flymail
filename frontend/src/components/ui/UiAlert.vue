<template>
  <div class="ui-alert" :class="`ui-alert--${tone}`" :role="tone === 'danger' ? 'alert' : 'status'">
    <span class="ui-alert__mark" aria-hidden="true">{{ mark }}</span>
    <div class="ui-alert__content">
      <strong v-if="title">{{ title }}</strong>
      <slot />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';

const props = withDefaults(defineProps<{
  tone?: 'info' | 'success' | 'warning' | 'danger';
  title?: string;
}>(), {
  tone: 'info',
  title: '',
});

const mark = computed(() => ({
  info: 'i',
  success: '✓',
  warning: '!',
  danger: '!',
}[props.tone]));
</script>

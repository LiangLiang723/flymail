<template>
  <div class="ui-segmented" role="group" :aria-label="label">
    <button
      v-for="option in options"
      :key="option.value"
      class="ui-segmented__item"
      :class="{ 'is-active': option.value === modelValue }"
      type="button"
      :disabled="option.disabled"
      :aria-pressed="option.value === modelValue"
      @click="emit('update:modelValue', option.value)"
    >
      <span>{{ option.label }}</span>
      <span v-if="option.count !== undefined" class="ui-segmented__count">{{ option.count }}</span>
    </button>
  </div>
</template>

<script setup lang="ts">
interface SegmentOption {
  value: string;
  label: string;
  count?: number;
  disabled?: boolean;
}

defineProps<{
  modelValue: string;
  options: SegmentOption[];
  label: string;
}>();

const emit = defineEmits<{
  'update:modelValue': [value: string];
}>();
</script>

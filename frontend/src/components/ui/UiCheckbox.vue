<template>
  <label class="ui-checkbox" :class="{ 'is-disabled': disabled }">
    <input
      class="ui-checkbox__input"
      type="checkbox"
      :checked="checked"
      :disabled="disabled"
      :aria-label="label"
      @change="handleChange"
    />
    <span class="ui-checkbox__box" aria-hidden="true">
      <svg viewBox="0 0 16 16" fill="none">
        <path d="m3.5 8 3 3 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    </span>
    <span class="ui-checkbox__label"><slot /></span>
  </label>
</template>

<script setup lang="ts">
import { computed } from 'vue';

type CheckboxItem = string | number;

const props = withDefaults(defineProps<{
  modelValue: boolean | CheckboxItem[];
  label: string;
  value?: CheckboxItem;
  disabled?: boolean;
}>(), {
  value: undefined,
  disabled: false,
});

const emit = defineEmits<{
  (event: 'update:modelValue', value: boolean | CheckboxItem[]): void;
  (event: 'change', value: boolean | CheckboxItem[]): void;
}>();

const checked = computed(() => {
  if (Array.isArray(props.modelValue)) {
    return props.value !== undefined && props.modelValue.includes(props.value);
  }
  return props.modelValue;
});

function handleChange(event: Event) {
  const nextChecked = (event.currentTarget as HTMLInputElement).checked;
  let nextValue: boolean | CheckboxItem[];

  if (Array.isArray(props.modelValue)) {
    if (props.value === undefined) return;
    nextValue = nextChecked
      ? [...props.modelValue, props.value]
      : props.modelValue.filter((item) => item !== props.value);
  } else {
    nextValue = nextChecked;
  }

  emit('update:modelValue', nextValue);
  emit('change', nextValue);
}
</script>

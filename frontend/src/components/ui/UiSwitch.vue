<template>
  <label class="ui-switch" :class="{ 'is-disabled': disabled, 'has-label': Boolean($slots.default) }">
    <input
      class="ui-switch__input"
      type="checkbox"
      role="switch"
      :checked="modelValue"
      :disabled="disabled"
      :aria-label="label"
      :aria-describedby="describedBy"
      :aria-busy="busy || undefined"
      @change="handleChange"
    />
    <span class="ui-switch__track" aria-hidden="true">
      <span class="ui-switch__knob"></span>
    </span>
    <span v-if="$slots.default" class="ui-switch__label"><slot /></span>
  </label>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  modelValue: boolean;
  label: string;
  disabled?: boolean;
  busy?: boolean;
  describedBy?: string;
}>(), {
  disabled: false,
  busy: false,
  describedBy: undefined,
});

const emit = defineEmits<{
  (event: 'update:modelValue', value: boolean): void;
  (event: 'change', value: boolean): void;
}>();

function handleChange(event: Event) {
  const value = (event.currentTarget as HTMLInputElement).checked;
  emit('update:modelValue', value);
  emit('change', value);
}
</script>

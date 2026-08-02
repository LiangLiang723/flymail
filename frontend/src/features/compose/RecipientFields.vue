<script setup lang="ts">
import { computed } from 'vue';

import type { ComposeRecipients } from './compose-state.ts';

const props = defineProps<{ modelValue: ComposeRecipients }>();
const emit = defineEmits<{ 'update:modelValue': [value: ComposeRecipients] }>();

function parse(value: string) {
  return value.split(/[;,\n]/).map((address) => address.trim()).filter(Boolean).map((address) => ({ address, display_name: '' }));
}
function join(key: keyof ComposeRecipients) {
  return computed({
    get: () => props.modelValue[key].map((recipient) => recipient.address).join(', '),
    set: (value: string) => emit('update:modelValue', { ...props.modelValue, [key]: parse(value) }),
  });
}
const to = join('to');
const cc = join('cc');
const bcc = join('bcc');
</script>

<template>
  <fieldset class="v2-recipient-fields">
    <legend>收件人</legend>
    <label><span>收件人</span><input v-model="to" type="text" autocomplete="off" inputmode="email" /></label>
    <label><span>抄送</span><input v-model="cc" type="text" autocomplete="off" inputmode="email" /></label>
    <label><span>密送</span><input v-model="bcc" type="text" autocomplete="off" inputmode="email" /></label>
  </fieldset>
</template>

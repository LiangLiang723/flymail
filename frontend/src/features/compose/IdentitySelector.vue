<script setup lang="ts">
import type { IdentityChoice } from './compose-state.ts';

const props = defineProps<{
  modelValue: string;
  identities: IdentityChoice[];
  contentChanged?: boolean;
}>();
const emit = defineEmits<{
  'update:modelValue': [identityId: string];
  identityChange: [identity: IdentityChoice];
}>();

function change(event: Event) {
  const nextId = (event.target as HTMLSelectElement).value;
  const next = props.identities.find((identity) => identity.id === nextId);
  if (!next) return;
  if (props.contentChanged && !window.confirm('切换发件身份会更新签名和 Reply-To，是否继续？')) {
    (event.target as HTMLSelectElement).value = props.modelValue;
    return;
  }
  emit('update:modelValue', nextId);
  emit('identityChange', next);
}
</script>

<template>
  <label class="v2-identity-selector">
    <span>发件身份</span>
    <select :value="modelValue" @change="change">
      <option v-for="identity in identities" :key="identity.id" :value="identity.id">
        {{ identity.id }}
      </option>
    </select>
  </label>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue';

import { apiClient } from '../../shared/api/client.ts';
import { mergeTypedRecipient, type RecipientSuggestion } from './contact-state.ts';

const props = defineProps<{ modelValue: string }>();
const emit = defineEmits<{ 'update:modelValue': [value: string]; select: [value: RecipientSuggestion] }>();
const items = ref<RecipientSuggestion[]>([]);
const active = ref(-1);
let controller: AbortController | undefined;

watch(() => props.modelValue, async (value) => {
  controller?.abort();
  if (!value.trim()) { items.value = []; return; }
  controller = new AbortController();
  try {
    const response = await apiClient.request<{ items: RecipientSuggestion[] }>({ method: 'GET', path: '/api/v2/contacts/autocomplete', query: { q: value }, signal: controller.signal });
    items.value = mergeTypedRecipient(value, response.items);
  } catch (error: unknown) {
    if (!(error instanceof DOMException && error.name === 'AbortError')) items.value = mergeTypedRecipient(value, []);
  }
});

function keydown(event: KeyboardEvent) {
  if (event.key === 'ArrowDown') { event.preventDefault(); active.value = Math.min(items.value.length - 1, active.value + 1); }
  else if (event.key === 'ArrowUp') { event.preventDefault(); active.value = Math.max(0, active.value - 1); }
  else if (event.key === 'Enter' && active.value >= 0) { event.preventDefault(); emit('select', items.value[active.value]); }
}
</script>

<template>
  <div class="v2-contact-autocomplete">
    <input :value="modelValue" type="email" autocomplete="off" @input="emit('update:modelValue', ($event.target as HTMLInputElement).value)" @keydown="keydown" />
    <ul v-if="items.length" role="listbox" aria-label="联系人建议">
      <li v-for="(item, index) in items" :key="item.address" role="option" :aria-selected="index === active"><button type="button" @click="emit('select', item)">{{ item.display_name || item.address }} · {{ item.address }}</button></li>
    </ul>
  </div>
</template>

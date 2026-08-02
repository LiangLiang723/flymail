<script setup lang="ts">
import { ref } from 'vue';

interface Suggestion { kind: string; value: string; label: string }
const props = defineProps<{ modelValue: string; suggestions: Suggestion[] }>();
const emit = defineEmits<{ 'update:modelValue': [value: string]; submit: []; suggestion: [value: string] }>();
const active = ref(-1);

function keydown(event: KeyboardEvent) {
  if (event.key === 'ArrowDown') { event.preventDefault(); active.value = Math.min(props.suggestions.length - 1, active.value + 1); }
  else if (event.key === 'ArrowUp') { event.preventDefault(); active.value = Math.max(-1, active.value - 1); }
  else if (event.key === 'Enter') {
    event.preventDefault();
    if (active.value >= 0) emit('suggestion', props.suggestions[active.value].value);
    else emit('submit');
  } else if (event.key === 'Escape') active.value = -1;
}
</script>

<template>
  <div class="v2-search-bar">
    <label><span class="v2-sr-only">搜索邮件</span><input :value="modelValue" type="search" placeholder="搜索主题、发件人和已缓存正文" @input="emit('update:modelValue', ($event.target as HTMLInputElement).value)" @keydown="keydown" /></label>
    <button type="button" @click="emit('submit')">搜索</button>
    <ul v-if="suggestions.length" role="listbox" aria-label="搜索建议">
      <li v-for="(item, index) in suggestions" :key="`${item.kind}:${item.value}`" role="option" :aria-selected="index === active">
        <button type="button" @click="emit('suggestion', item.value)">{{ item.label }}</button>
      </li>
    </ul>
  </div>
</template>

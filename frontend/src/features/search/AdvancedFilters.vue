<script setup lang="ts">
import { ref } from 'vue';

import { validateSearchFilters, type SearchFilters } from './search-state.ts';

const props = defineProps<{ modelValue: SearchFilters; open: boolean }>();
const emit = defineEmits<{ apply: [value: SearchFilters]; cancel: []; close: [] }>();
const draft = ref<SearchFilters>({ ...props.modelValue });
function apply() { emit('apply', validateSearchFilters(draft.value)); }
</script>

<template>
  <section v-if="open" class="v2-advanced-filters" role="dialog" aria-modal="true" aria-labelledby="advanced-filter-title">
    <h2 id="advanced-filter-title">高级搜索条件</h2>
    <label>发件人<input :value="draft.from_addresses?.join(', ')" @input="draft.from_addresses = ($event.target as HTMLInputElement).value.split(',').map(item => item.trim()).filter(Boolean)" /></label>
    <label>收件人<input :value="draft.to_addresses?.join(', ')" @input="draft.to_addresses = ($event.target as HTMLInputElement).value.split(',').map(item => item.trim()).filter(Boolean)" /></label>
    <label>起始日期<input type="date" @input="draft.date_from = ($event.target as HTMLInputElement).valueAsDate ? ($event.target as HTMLInputElement).valueAsDate!.getTime() / 1000 : null" /></label>
    <label>结束日期<input type="date" @input="draft.date_to = ($event.target as HTMLInputElement).valueAsDate ? ($event.target as HTMLInputElement).valueAsDate!.getTime() / 1000 + 86399 : null" /></label>
    <label>阅读状态<select v-model="draft.is_read"><option :value="null">不限</option><option :value="false">未读</option><option :value="true">已读</option></select></label>
    <label><input v-model="draft.is_starred" type="checkbox" :true-value="true" :false-value="null" /> 已加星</label>
    <label><input v-model="draft.has_attachment" type="checkbox" :true-value="true" :false-value="null" /> 含附件</label>
    <div><button type="button" @click="apply">应用</button><button type="button" @click="emit('cancel'); emit('close')">取消</button></div>
  </section>
</template>

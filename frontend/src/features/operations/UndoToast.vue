<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue';

import { undoRemainingMs } from './operation-actions.ts';

const props = defineProps<{ operationId: string; expiresAt: number; message?: string }>();
const emit = defineEmits<{ undo: [operationId: string]; dismiss: [] }>();
const now = ref(Date.now());
const timer = setInterval(() => { now.value = Date.now(); }, 250);
const remaining = computed(() => undoRemainingMs(props.expiresAt, now.value));
onBeforeUnmount(() => clearInterval(timer));
</script>

<template>
  <aside class="v2-undo-toast" role="status" aria-live="polite">
    <span>{{ message || '操作已提交' }}；撤销到期时间剩余 {{ Math.ceil(remaining / 1000) }} 秒</span>
    <button type="button" :disabled="remaining <= 0" @click="emit('undo', operationId)">撤销</button>
    <button type="button" aria-label="关闭提示" @click="emit('dismiss')">关闭</button>
  </aside>
</template>

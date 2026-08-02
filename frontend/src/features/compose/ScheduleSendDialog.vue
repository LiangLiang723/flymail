<script setup lang="ts">
import { ref } from 'vue';

import { scheduleToEpochSeconds } from './compose-state.ts';

const emit = defineEmits<{ schedule: [epochSeconds: number]; close: [] }>();
const value = ref('');
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
const timezoneOffsetMinutes = -new Date().getTimezoneOffset();

function submit() {
  if (!value.value) return;
  emit('schedule', scheduleToEpochSeconds(value.value, timezoneOffsetMinutes));
}
</script>

<template>
  <form class="v2-schedule-dialog" role="dialog" aria-modal="true" aria-labelledby="schedule-title" @submit.prevent="submit">
    <h2 id="schedule-title">定时发送</h2>
    <p>时区：{{ timezone }}</p>
    <label>发送时间<input v-model="value" type="datetime-local" required /></label>
    <button type="submit">确认时间</button>
    <button type="button" @click="emit('close')">取消</button>
  </form>
</template>

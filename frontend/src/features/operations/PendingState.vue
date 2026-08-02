<script setup lang="ts">
defineProps<{
  status: string;
  partialResults?: Array<{ message_id: string; status: string; message?: string }>;
}>();
</script>

<template>
  <section class="v2-pending-state" role="status">
    <p>操作状态：{{ status }}</p>
    <ul v-if="partialResults?.length" aria-label="部分操作结果">
      <li v-for="item in partialResults" :key="item.message_id">
        <strong>{{ item.message_id }}</strong>：{{ item.status }}<span v-if="item.message">（{{ item.message }}）</span>
      </li>
    </ul>
  </section>
</template>

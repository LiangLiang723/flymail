<script setup lang="ts">
defineProps<{ count: number; allowed: boolean }>();
const emit = defineEmits<{ change: [allowed: boolean] }>();
</script>

<template>
  <aside v-if="count" class="v2-remote-image-control" role="status">
    <span>{{ allowed ? '已允许本封邮件的远程图片' : `已阻止 ${count} 张远程图片` }}</span>
    <button type="button" @click="emit('change', !allowed)">
      {{ allowed ? '重新阻止' : '显示远程图片' }}
    </button>
  </aside>
</template>

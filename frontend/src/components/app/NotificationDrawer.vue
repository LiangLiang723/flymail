<template>
  <Transition name="drawer">
    <div v-if="open" class="notification-overlay" @click.self="$emit('close')">
      <aside class="notification-drawer" aria-label="通知中心">
        <header class="notification-header">
          <div>
            <h3>通知中心</h3>
            <span>{{ unreadCount }} 条未读</span>
          </div>
          <button type="button" aria-label="关闭通知中心" @click="$emit('close')">×</button>
        </header>
        <div class="notification-tools">
          <button type="button" @click="$emit('mark-all-read')">全部已读</button>
          <button type="button" class="danger-text" @click="$emit('clear')">清空</button>
        </div>
        <div v-if="!notifications.length" class="notification-empty">暂无通知</div>
        <div v-else class="notification-list">
          <button
            v-for="item in notifications"
            :key="item.id"
            type="button"
            class="notification-item"
            :class="{ unread: !item.read }"
            @click="$emit('open-item', item)"
          >
            <span class="notification-dot"></span>
            <span class="notification-content">
              <strong>{{ item.subject || item.message || titleFor(item) }}</strong>
              <small>{{ item.from_addr || item.email }}<template v-if="item.batch_count && item.batch_count > 1"> · {{ item.batch_count }} 封</template></small>
              <em v-if="item.body_preview">{{ item.body_preview }}</em>
            </span>
            <time>{{ formatTime(item.time) }}</time>
          </button>
        </div>
      </aside>
    </div>
  </Transition>
</template>

<script setup lang="ts">
defineProps<{
  open: boolean;
  notifications: any[];
  unreadCount: number;
  titleFor: (item: any) => string;
  formatTime: (value: number) => string;
}>();

defineEmits<{
  close: [];
  'mark-all-read': [];
  clear: [];
  'open-item': [item: any];
}>();
</script>

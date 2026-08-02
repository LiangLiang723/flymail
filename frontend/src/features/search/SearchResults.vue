<script setup lang="ts">
import type { SearchResultItem } from './search-state.ts';

defineProps<{ items: SearchResultItem[]; loading?: boolean; nextCursor?: string | null }>();
const emit = defineEmits<{ open: [threadId: string]; loadMore: [] }>();
</script>

<template>
  <section class="v2-search-results" aria-label="搜索结果">
    <article v-for="item in items" :key="item.thread_id" tabindex="0" @click="emit('open', item.thread_id)" @keydown.enter="emit('open', item.thread_id)">
      <div><strong>{{ item.subject || '（无主题）' }}</strong><time>{{ new Date(item.received_at * 1000).toLocaleString() }}</time></div>
      <p>{{ item.snippet || (item.matched_field === 'body' ? '正文缓存已释放，metadata 元数据仍可搜索。' : '匹配元数据') }}</p>
      <small>匹配字段：{{ item.matched_field }} · 消息 {{ item.matched_message_id }}</small>
    </article>
    <p v-if="!items.length && !loading">没有匹配结果。</p>
    <button v-if="nextCursor" type="button" :disabled="loading" @click="emit('loadMore')">加载更多</button>
  </section>
</template>

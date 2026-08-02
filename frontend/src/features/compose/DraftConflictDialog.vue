<script setup lang="ts">
import type { ComposeModel } from './compose-state.ts';

defineProps<{ local: ComposeModel; remote: ComposeModel }>();
const emit = defineEmits<{ resolve: [choice: 'local' | 'remote']; close: [] }>();
</script>

<template>
  <section class="v2-draft-conflict" role="dialog" aria-modal="true" aria-labelledby="draft-conflict-title">
    <h2 id="draft-conflict-title">草稿版本冲突</h2>
    <p>本地版本和服务器版本都已保留，不会自动覆盖。</p>
    <div class="v2-draft-conflict__versions">
      <article><h3>local 本地版本</h3><strong>{{ local.subject || '（无主题）' }}</strong><p>{{ local.body_text.slice(0, 240) }}</p></article>
      <article><h3>remote 服务器版本</h3><strong>{{ remote.subject || '（无主题）' }}</strong><p>{{ remote.body_text.slice(0, 240) }}</p></article>
    </div>
    <button type="button" @click="emit('resolve', 'local')">保留本地内容并另存</button>
    <button type="button" @click="emit('resolve', 'remote')">使用服务器版本</button>
    <button type="button" @click="emit('close')">稍后处理</button>
  </section>
</template>

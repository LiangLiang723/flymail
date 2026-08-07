<template>
  <div class="mail-search-bar">
    <div class="mail-search-main">
      <div class="mail-search-input-wrap">
        <svg class="mail-search-icon" viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="11" cy="11" r="7"></circle>
          <path d="m20 20-3.5-3.5"></path>
        </svg>
        <input
          :value="modelValue.keyword"
          class="mail-search-input"
          type="search"
          autocomplete="off"
          placeholder="搜索邮件，支持 from:、to:、subject:、is:unread…"
          @input="updateText('keyword', $event)"
          @keydown.enter.prevent="emit('search')"
        />
      </div>
      <button
        type="button"
        class="mail-search-action"
        :class="{ active: advancedOpen || activeLabels.length > 0 }"
        :aria-expanded="advancedOpen"
        @click="advancedOpen = !advancedOpen"
      >
        筛选<span v-if="activeLabels.length" class="filter-count">{{ activeLabels.length }}</span>
      </button>
      <button type="button" class="mail-search-primary" @click="emit('search')">搜索</button>
      <button v-if="hasFilters" type="button" class="mail-search-clear" @click="emit('clear')">清除</button>
    </div>

    <div v-if="activeLabels.length" class="mail-search-summary" aria-label="已应用筛选">
      <span v-for="label in activeLabels" :key="label" class="search-chip">{{ label }}</span>
    </div>

    <div v-if="advancedOpen" class="mail-search-panel">
      <label class="search-field">
        <span>发件人</span>
        <input :value="modelValue.fromAddr" placeholder="姓名或邮箱" @input="updateText('fromAddr', $event)" />
      </label>
      <label class="search-field">
        <span>收件人</span>
        <input :value="modelValue.toAddr" placeholder="To / Cc" @input="updateText('toAddr', $event)" />
      </label>
      <label class="search-field">
        <span>主题</span>
        <input :value="modelValue.subject" placeholder="主题包含" @input="updateText('subject', $event)" />
      </label>
      <label class="search-field">
        <span>正文</span>
        <input :value="modelValue.body" placeholder="正文包含" @input="updateText('body', $event)" />
      </label>
      <label class="search-field">
        <span>开始日期</span>
        <input :value="modelValue.after" type="date" @input="updateText('after', $event)" />
      </label>
      <label class="search-field">
        <span>早于日期</span>
        <input :value="modelValue.before" type="date" @input="updateText('before', $event)" />
      </label>
      <label class="search-field">
        <span>阅读状态</span>
        <select :value="modelValue.readFilter" @change="updateReadFilter">
          <option value="">不限</option>
          <option value="unread">未读</option>
          <option value="read">已读</option>
        </select>
      </label>
      <div class="search-checks">
        <label>
          <input :checked="modelValue.attachmentOnly" type="checkbox" @change="updateBoolean('attachmentOnly', $event)" />
          <span>有附件</span>
        </label>
        <label>
          <input :checked="modelValue.starredOnly" type="checkbox" @change="updateBoolean('starredOnly', $event)" />
          <span>已星标</span>
        </label>
      </div>
      <div class="mail-search-panel-actions">
        <button type="button" class="mail-search-clear" @click="emit('clear')">重置条件</button>
        <button type="button" class="mail-search-primary" @click="applyAdvanced">应用筛选</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import type { MailSearchState } from '../../types/mail';
import { hasMailSearchFilters } from '../../utils/mail-search';

const props = defineProps<{ modelValue: MailSearchState }>();
const emit = defineEmits<{
  (event: 'update:modelValue', value: MailSearchState): void;
  (event: 'search'): void;
  (event: 'clear'): void;
}>();

const advancedOpen = ref(false);
type TextField = 'keyword' | 'fromAddr' | 'toAddr' | 'subject' | 'body' | 'after' | 'before';
type BooleanField = 'attachmentOnly' | 'starredOnly';

const hasFilters = computed(() => hasMailSearchFilters(props.modelValue));
const activeLabels = computed(() => {
  const state = props.modelValue;
  const labels: string[] = [];
  if (state.fromAddr) labels.push(`发件人：${state.fromAddr}`);
  if (state.toAddr) labels.push(`收件人：${state.toAddr}`);
  if (state.subject) labels.push(`主题：${state.subject}`);
  if (state.body) labels.push(`正文：${state.body}`);
  if (state.after) labels.push(`从 ${state.after}`);
  if (state.before) labels.push(`早于 ${state.before}`);
  if (state.readFilter === 'unread') labels.push('未读');
  if (state.readFilter === 'read') labels.push('已读');
  if (state.attachmentOnly) labels.push('有附件');
  if (state.starredOnly) labels.push('已星标');
  return labels;
});

function updateText(field: TextField, event: Event) {
  const value = (event.target as HTMLInputElement).value;
  emit('update:modelValue', { ...props.modelValue, [field]: value });
}

function updateReadFilter(event: Event) {
  const value = (event.target as HTMLSelectElement).value as MailSearchState['readFilter'];
  emit('update:modelValue', { ...props.modelValue, readFilter: value });
}

function updateBoolean(field: BooleanField, event: Event) {
  const value = (event.target as HTMLInputElement).checked;
  emit('update:modelValue', { ...props.modelValue, [field]: value });
}

function applyAdvanced() {
  advancedOpen.value = false;
  emit('search');
}
</script>

<style scoped>
.mail-search-bar {
  position: relative;
  min-width: min(560px, 52vw);
}

.mail-search-main {
  display: flex;
  align-items: center;
  gap: 8px;
}

.mail-search-input-wrap {
  position: relative;
  flex: 1;
  min-width: 220px;
}

.mail-search-icon {
  position: absolute;
  left: 11px;
  top: 50%;
  width: 16px;
  height: 16px;
  transform: translateY(-50%);
  fill: none;
  stroke: var(--text-tertiary);
  stroke-width: 2;
  pointer-events: none;
}

.mail-search-input,
.search-field input,
.search-field select {
  width: 100%;
  min-height: 36px;
  border: 1px solid var(--border-color);
  border-radius: 9px;
  background: var(--bg-primary);
  color: var(--text-primary);
  font: inherit;
  outline: none;
}

.mail-search-input {
  padding: 7px 10px 7px 34px;
}

.mail-search-input:focus,
.search-field input:focus,
.search-field select:focus {
  border-color: var(--accent-blue);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent-blue) 14%, transparent);
}

.mail-search-action,
.mail-search-primary,
.mail-search-clear {
  min-height: 34px;
  border-radius: 8px;
  padding: 0 12px;
  border: 1px solid var(--border-color);
  background: var(--bg-primary);
  color: var(--text-secondary);
  font: inherit;
  cursor: pointer;
  white-space: nowrap;
}

.mail-search-action.active {
  color: var(--accent-blue);
  border-color: color-mix(in srgb, var(--accent-blue) 45%, var(--border-color));
  background: color-mix(in srgb, var(--accent-blue) 7%, var(--bg-primary));
}

.mail-search-primary {
  border-color: var(--accent-blue);
  background: var(--accent-blue);
  color: var(--ui-text-inverse);
}

.mail-search-clear {
  color: var(--text-tertiary);
}

.filter-count {
  display: inline-grid;
  place-items: center;
  min-width: 18px;
  height: 18px;
  margin-left: 5px;
  border-radius: 9px;
  background: var(--accent-blue);
  color: var(--ui-text-inverse);
  font-size: 11px;
}

.mail-search-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 7px;
}

.search-chip {
  max-width: 190px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 4px 8px;
  border-radius: 999px;
  background: var(--bg-secondary);
  color: var(--text-secondary);
  font-size: 12px;
}

.mail-search-panel {
  position: absolute;
  z-index: 30;
  top: calc(100% + 8px);
  right: 0;
  width: min(640px, 88vw);
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--border-color);
  border-radius: 12px;
  background: var(--bg-primary);
  box-shadow: var(--ui-shadow-md);
}

.search-field {
  display: grid;
  gap: 6px;
  color: var(--text-secondary);
  font-size: 12px;
}

.search-field input,
.search-field select {
  padding: 7px 9px;
}

.search-checks {
  display: flex;
  align-items: end;
  gap: 16px;
  padding-bottom: 7px;
}

.search-checks label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-secondary);
  font-size: 13px;
}

.mail-search-panel-actions {
  grid-column: 1 / -1;
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

@media (max-width: 900px) {
  .mail-search-bar {
    min-width: 0;
    width: 100%;
  }

  .mail-search-main {
    gap: 6px;
  }

  .mail-search-input-wrap {
    min-width: 0;
  }

  .mail-search-primary {
    display: none;
  }
}

@media (max-width: 640px) {
  .mail-search-panel {
    grid-template-columns: 1fr;
    position: fixed;
    top: 64px;
    left: 12px;
    right: 12px;
    width: auto;
    max-height: calc(100vh - 84px);
    overflow: auto;
  }

  .mail-search-clear {
    display: none;
  }

  .mail-search-panel .mail-search-clear {
    display: inline-flex;
    align-items: center;
  }
}
</style>

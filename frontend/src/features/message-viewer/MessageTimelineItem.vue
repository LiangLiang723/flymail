<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';

import type { BodyResponse, MessageSummary } from '../../entities/message/types.ts';
import { apiClient } from '../../shared/api/client.ts';
import { normalizeApiError } from '../../shared/api/errors.ts';
import AttachmentList from './AttachmentList.vue';
import MessageBody from './MessageBody.vue';
import { BodyRequestRegistry, bodyStateMessage } from './body-state.ts';
import { exportSanitizedMailToPdf } from './export-pdf.ts';

const props = defineProps<{ message: MessageSummary; expanded: boolean }>();
const emit = defineEmits<{ toggle: []; openImage: [src: string] }>();
const bodyContainer = ref<HTMLElement | null>(null);
const state = reactive<{ loading: boolean; response?: BodyResponse; error?: string }>({ loading: false });
const displayState = computed(() => state.response?.state || props.message.body_state);

const registry = new BodyRequestRegistry(async (messageId) => apiClient.request<BodyResponse>({
  method: 'POST',
  path: `/api/v2/messages/${encodeURIComponent(messageId)}/body/request`,
}));

async function loadBody() {
  if (state.loading || state.response?.state === 'ready') return;
  state.loading = true;
  state.error = undefined;
  try {
    if (props.message.body_state === 'ready') {
      state.response = await apiClient.request<BodyResponse>({
        method: 'GET',
        path: `/api/v2/messages/${encodeURIComponent(props.message.id)}/body`,
      });
    } else {
      state.response = await registry.request(props.message.id);
    }
  } catch (value: unknown) {
    state.error = normalizeApiError(value).message;
  } finally {
    state.loading = false;
  }
}

async function exportPdf() {
  if (!bodyContainer.value) return;
  await exportSanitizedMailToPdf({ subject: props.message.subject, source: bodyContainer.value });
}

watch(() => props.expanded, (expanded) => { if (expanded) void loadBody(); }, { immediate: true });
</script>

<template>
  <article class="v2-message-item">
    <button type="button" class="v2-message-item__header" :aria-expanded="expanded" @click="emit('toggle')">
      <span>
        <strong>{{ message.from[0]?.name || message.from[0]?.address || '未知发件人' }}</strong>
        <small>{{ message.from[0]?.address }}</small>
      </span>
      <span>{{ message.received_at ? new Date(message.received_at * 1000).toLocaleString() : '' }}</span>
    </button>

    <div v-if="expanded" class="v2-message-item__content">
      <div class="v2-message-actions">
        <button type="button" :disabled="!state.response || state.response.state !== 'ready'" @click="exportPdf">导出 PDF</button>
      </div>
      <p v-if="state.loading" role="status">正在读取本地正文状态…</p>
      <p v-else-if="state.error" class="v2-error" role="alert">{{ state.error }} <button type="button" @click="loadBody">重试</button></p>
      <div v-else-if="displayState !== 'ready'" class="v2-body-state" role="status">
        <p>{{ bodyStateMessage(displayState) }}</p>
        <button v-if="displayState === 'failed' || displayState === 'evicted' || displayState === 'unavailable'" type="button" @click="loadBody">重试获取正文</button>
      </div>
      <div v-else ref="bodyContainer">
        <MessageBody :html="state.response?.html" :text="state.response?.text" @open-image="emit('openImage', $event)" />
      </div>
      <AttachmentList :attachments="message.attachments.filter((attachment) => !attachment.is_inline)" />
    </div>
  </article>
</template>

<script setup lang="ts">
import { reactive } from 'vue';

import type { AttachmentSummary } from '../../entities/message/types.ts';
import { apiClient } from '../../shared/api/client.ts';
import { normalizeApiError } from '../../shared/api/errors.ts';

defineProps<{ attachments: AttachmentSummary[] }>();
const progress = reactive<Record<string, { status: string; error?: string }>>({});

function isDangerousInline(contentType?: string): boolean {
  return contentType === 'application/svg+xml' || contentType === 'text/html';
}

async function requestAttachment(attachment: AttachmentSummary) {
  progress[attachment.id] = { status: '正在准备本地下载…' };
  try {
    const response = await apiClient.request<{ state: string; download_url?: string; task_id?: string }>({
      method: 'POST',
      path: `/api/v2/attachments/${encodeURIComponent(attachment.id)}/request`,
    });
    if (response.download_url) {
      window.location.assign(response.download_url);
      progress[attachment.id] = { status: '下载已开始' };
    } else {
      progress[attachment.id] = { status: response.state === 'ready' ? '附件已就绪' : '附件获取任务已排队' };
    }
  } catch (value: unknown) {
    progress[attachment.id] = { status: '附件获取失败', error: normalizeApiError(value).message };
  }
}
</script>

<template>
  <section v-if="attachments.length" class="v2-attachment-list" aria-label="附件">
    <h4>附件</h4>
    <ul>
      <li v-for="attachment in attachments" :key="attachment.id">
        <div>
          <strong>{{ attachment.filename }}</strong>
          <small>{{ attachment.content_type || '未知类型' }} · {{ attachment.size_bytes || 0 }} 字节</small>
          <small v-if="isDangerousInline(attachment.content_type)">为安全起见，此类型不会在页面内嵌显示。</small>
        </div>
        <button type="button" @click="requestAttachment(attachment)">下载</button>
        <p v-if="progress[attachment.id]" role="status">
          {{ progress[attachment.id].status }}
          <span v-if="progress[attachment.id].error" class="v2-error">{{ progress[attachment.id].error }}</span>
        </p>
      </li>
    </ul>
  </section>
</template>

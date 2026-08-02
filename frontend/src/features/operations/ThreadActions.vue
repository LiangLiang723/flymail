<script setup lang="ts">
import { reactive, ref } from 'vue';

import type { OperationAccepted } from '../../entities/operation/types.ts';
import type { ThreadDetailResponse, ThreadProjection } from '../../shared/api/generated.ts';
import { apiClient } from '../../shared/api/client.ts';
import { normalizeApiError } from '../../shared/api/errors.ts';
import PendingState from './PendingState.vue';
import UndoToast from './UndoToast.vue';
import { OperationCommandAdapter, canPermanentlyDelete } from './operation-actions.ts';

const props = defineProps<{
  threadId: string;
  targetName: string;
  mailbox?: { semantic_mailbox?: string; account_id?: string; native_label?: string };
}>();
const emit = defineEmits<{ projection: [projection: ThreadProjection] }>();
const state = reactive<{ busy: string; error?: string; pending?: OperationAccepted }>({ busy: '' });
const typedDeleteName = ref('');
const showDelete = ref(false);

const adapter = new OperationCommandAdapter({
  submit: (command) => apiClient.request<OperationAccepted>({ method: 'POST', path: '/api/v2/operations', body: command }),
  patchProjection: (_targetId, projection) => emit('projection', projection),
  fetchAuthoritativeProjection: async (threadId) => {
    const detail = await apiClient.request<ThreadDetailResponse>({ method: 'GET', path: `/api/v2/threads/${encodeURIComponent(threadId)}` });
    return detail.projection;
  },
});

async function execute(operationType: string, desiredState: Record<string, unknown>, confirmationToken?: string) {
  if (state.busy) return;
  state.busy = operationType;
  state.error = undefined;
  try {
    state.pending = await adapter.execute({
      target_type: 'thread', target_id: props.threadId, operation_type: operationType,
      desired_state: desiredState, confirmation_token: confirmationToken,
    });
  } catch (value: unknown) {
    state.error = normalizeApiError(value).message;
  } finally {
    state.busy = '';
  }
}

async function permanentlyDelete() {
  if (!canPermanentlyDelete(props.targetName, typedDeleteName.value)) return;
  const confirmation = await apiClient.request<{ confirmation_token: string; expires_at: number }>({
    method: 'POST', path: '/api/v2/operations/permanent-delete-confirmation',
    body: { target_type: 'thread', target_id: props.threadId },
  });
  await execute('permanent_delete', { confirmed_name: typedDeleteName.value }, confirmation.confirmation_token);
  showDelete.value = false;
  typedDeleteName.value = '';
}

async function undo(operationId: string) {
  await apiClient.request({
    method: 'POST', path: `/api/v2/operations/${encodeURIComponent(operationId)}/undo`,
    body: { idempotency_key: crypto.randomUUID() },
  });
  state.pending = undefined;
}

async function markAllRead() {
  if (!props.mailbox || state.busy) return;
  state.busy = 'mark-all-read';
  try {
    await apiClient.request({
      method: 'POST', path: '/api/v2/operations/mark-all-read',
      body: { ...props.mailbox, idempotency_key: crypto.randomUUID() },
    });
  } finally {
    state.busy = '';
  }
}
</script>

<template>
  <section class="v2-thread-actions" aria-label="会话操作">
    <div class="v2-thread-actions__toolbar v2-mobile-operation-toolbar">
      <button type="button" :disabled="!!state.busy" @click="execute('read', { read: true })">标为已读</button>
      <button type="button" :disabled="!!state.busy" @click="execute('star', { starred: true })">加星</button>
      <button type="button" :disabled="!!state.busy" @click="execute('archive', { archived: true })">归档</button>
      <button type="button" :disabled="!!state.busy" @click="execute('trash', { trashed: true })">移到已删除</button>
      <button v-if="mailbox" type="button" :disabled="!!state.busy" data-action="mark-all-read" @click="markAllRead">当前范围全部已读</button>
      <button type="button" class="v2-danger-button" @click="showDelete = true">永久删除…</button>
    </div>
    <p v-if="state.error" class="v2-error" role="alert">{{ state.error }}</p>
    <PendingState
      v-if="state.pending"
      status="pending"
      :partial-results="state.pending.partial_results"
    />
    <UndoToast
      v-if="state.pending?.operation_ids[0] && state.pending.undo_expires_at"
      :operation-id="state.pending.operation_ids[0]"
      :expires-at="state.pending.undo_expires_at"
      @undo="undo"
      @dismiss="state.pending = undefined"
    />
    <form v-if="showDelete" class="v2-delete-confirmation" @submit.prevent="permanentlyDelete">
      <p>永久删除不可撤销。请输入“{{ targetName }}”确认。</p>
      <input v-model="typedDeleteName" :aria-label="`输入 ${targetName} 以确认永久删除`" />
      <button type="submit" :disabled="!canPermanentlyDelete(targetName, typedDeleteName)">确认永久删除</button>
      <button type="button" @click="showDelete = false">取消</button>
    </form>
  </section>
</template>

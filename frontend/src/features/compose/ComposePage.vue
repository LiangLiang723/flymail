<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router';

import { useBootstrap } from '../../app/bootstrap.ts';
import { apiClient } from '../../shared/api/client.ts';
import { normalizeApiError } from '../../shared/api/errors.ts';
import ComposeEditor from './ComposeEditor.vue';
import DraftAttachments from './DraftAttachments.vue';
import DraftConflictDialog from './DraftConflictDialog.vue';
import IdentitySelector from './IdentitySelector.vue';
import RecipientFields from './RecipientFields.vue';
import ScheduleSendDialog from './ScheduleSendDialog.vue';
import ServerPathPicker from './ServerPathPicker.vue';
import {
  AutosaveController,
  chooseInitialIdentity,
  createComposeModel,
  type ComposeModel,
  type DraftAttachment,
  type DraftRecord,
  type IdentityChoice,
} from './compose-state.ts';

interface IdentityResponse {
  id: string;
  account_id: string;
  from_address: string;
  display_name: string;
  reply_to: string;
  signature_html: string;
  signature_text: string;
  is_default: boolean;
  is_verified: boolean;
}

const route = useRoute();
const router = useRouter();
const bootstrap = useBootstrap();
const controller = ref<AutosaveController>();
const identities = ref<IdentityChoice[]>([]);
const loading = ref(true);
const error = ref('');
const showServerPicker = ref(false);
const showSchedule = ref(false);
const sendOperationId = ref('');
const sendStatus = ref('');
const draft = computed(() => controller.value?.model);
const autosaveState = computed(() => controller.value?.state || 'clean');
const contentChanged = computed(() => Boolean(draft.value?.subject || draft.value?.body_text || draft.value?.body_html));

function payload(model: ComposeModel) {
  return {
    account_id: model.account_id,
    identity_id: model.identity_id,
    thread_id: model.thread_id,
    reply_to_message_id: model.reply_to_message_id,
    subject: model.subject,
    body_html: model.body_html,
    body_text: model.body_text,
    recipients: model.recipients,
    scheduled_at: model.scheduled_at,
  };
}

async function loadIdentities() {
  const accounts = bootstrap.state.data?.accounts || [];
  const responses = await Promise.all(accounts.map(async (account) => {
    const response = await apiClient.request<{ items: IdentityResponse[] }>({
      method: 'GET', path: `/api/v2/accounts/${encodeURIComponent(account.id)}/identities`,
    });
    return response.items.map((identity): IdentityChoice => ({
      id: identity.id,
      accountId: identity.account_id,
      isDefault: identity.is_default,
      signatureHtml: identity.signature_html,
      replyTo: identity.reply_to,
    }));
  }));
  identities.value = responses.flat();
}

async function createInitialDraft(): Promise<DraftRecord> {
  const draftId = typeof route.params.draftId === 'string' ? route.params.draftId : '';
  if (draftId) return apiClient.request<DraftRecord>({ method: 'GET', path: `/api/v2/drafts/${encodeURIComponent(draftId)}` });

  const replyMessageId = typeof route.query.reply === 'string' ? route.query.reply : '';
  if (replyMessageId) {
    const template = await apiClient.request<Omit<DraftRecord, 'id' | 'attachments' | 'version' | 'status' | 'send_state' | 'send_message_id' | 'created_at' | 'updated_at' | 'queued_at' | 'sent_at'>>({
      method: 'GET', path: `/api/v2/messages/${encodeURIComponent(replyMessageId)}/compose-template`, query: { mode: route.query.mode === 'forward' ? 'forward' : 'reply' },
    });
    return apiClient.request<DraftRecord>({ method: 'POST', path: '/api/v2/drafts', body: template });
  }

  const accountId = typeof route.query.account === 'string' ? route.query.account : (bootstrap.state.data?.accounts[0]?.id || '');
  const identityId = chooseInitialIdentity(identities.value, accountId);
  const identity = identities.value.find((item) => item.id === identityId);
  if (!accountId || !identityId) throw new Error('没有可用的发件账号或身份');
  return apiClient.request<DraftRecord>({
    method: 'POST', path: '/api/v2/drafts',
    body: {
      account_id: identity?.accountId || accountId,
      identity_id: identityId,
      thread_id: null,
      reply_to_message_id: null,
      subject: '',
      body_html: identity?.signatureHtml || '',
      body_text: '',
      recipients: { to: [], cc: [], bcc: [] },
      scheduled_at: null,
    },
  });
}

async function initialize() {
  loading.value = true;
  error.value = '';
  try {
    if (!bootstrap.state.data) await bootstrap.load();
    await loadIdentities();
    const initial = await createInitialDraft();
    controller.value = new AutosaveController({
      initial: createComposeModel(initial),
      save: (model, expectedVersion) => apiClient.request<DraftRecord>({
        method: 'PUT', path: `/api/v2/drafts/${encodeURIComponent(model.id)}`,
        body: { ...payload(model), expected_version: expectedVersion },
      }),
    });
    if (!route.params.draftId) await router.replace({ name: 'compose', params: { draftId: initial.id }, query: route.query });
  } catch (value: unknown) {
    error.value = normalizeApiError(value).message;
  } finally {
    loading.value = false;
  }
}

function update(patch: Partial<ComposeModel>) {
  controller.value?.update(patch);
}

function identityChanged(identity: IdentityChoice) {
  const current = draft.value;
  if (!current) return;
  update({
    account_id: identity.accountId,
    identity_id: identity.id,
    body_html: contentChanged.value ? current.body_html : (identity.signatureHtml || ''),
  });
}

function addAttachment(attachment: DraftAttachment) {
  if (!draft.value) return;
  update({ attachments: [...draft.value.attachments, attachment] });
}

function removeAttachment(attachmentId: string) {
  if (!draft.value) return;
  update({ attachments: draft.value.attachments.filter((item) => item.id !== attachmentId) });
}

async function send(scheduledAt?: number) {
  if (!controller.value || !draft.value) return;
  if (scheduledAt !== undefined) update({ scheduled_at: scheduledAt });
  await controller.value.flush();
  if (controller.value.state === 'conflict' || controller.value.state === 'failed') return;
  const response = await apiClient.request<{ operation_id: string }>({
    method: 'POST', path: `/api/v2/drafts/${encodeURIComponent(draft.value.id)}/send`,
    body: { idempotency_key: crypto.randomUUID() },
  });
  sendOperationId.value = response.operation_id;
  sendStatus.value = scheduledAt ? '定时发送已排队' : '发送已排队';
  showSchedule.value = false;
}

async function cancelSend() {
  if (!draft.value || !sendOperationId.value) return;
  await apiClient.request({
    method: 'POST', path: `/api/v2/drafts/${encodeURIComponent(draft.value.id)}/cancel-send`,
    body: { operation_id: sendOperationId.value },
  });
  sendOperationId.value = '';
  sendStatus.value = '排队发送已取消';
}

function beforeUnload(event: BeforeUnloadEvent) {
  if (!controller.value?.hasUnsavedChanges()) return;
  event.preventDefault();
  event.returnValue = '';
}

onBeforeRouteLeave(async () => {
  if (!controller.value?.hasUnsavedChanges()) return true;
  await controller.value.flush().catch(() => undefined);
  if (!controller.value.hasUnsavedChanges()) return true;
  return window.confirm('草稿仍有未保存更改。确定放弃并离开吗？');
});

onMounted(() => {
  window.addEventListener('beforeunload', beforeUnload);
  void initialize();
});
onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', beforeUnload);
  controller.value?.destroy();
});
</script>

<template>
  <main class="v2-compose-page">
    <header class="v2-compose-page__header">
      <div><p class="v2-eyebrow">写信</p><h1>{{ draft?.subject || '新邮件' }}</h1></div>
      <div class="v2-compose-page__actions">
        <span role="status">{{ autosaveState }}</span>
        <button type="button" :disabled="!draft" @click="send()">发送</button>
        <button type="button" :disabled="!draft" @click="showSchedule = true">定时发送</button>
        <button v-if="sendOperationId" type="button" @click="cancelSend">取消排队发送</button>
      </div>
    </header>

    <p v-if="loading" role="status">正在准备草稿…</p>
    <p v-else-if="error" class="v2-error" role="alert">{{ error }} <button type="button" @click="initialize">重试</button></p>
    <form v-else-if="draft" class="v2-compose-form" @submit.prevent="send()">
      <IdentitySelector
        :model-value="draft.identity_id"
        :identities="identities"
        :content-changed="contentChanged"
        @update:model-value="update({ identity_id: $event })"
        @identity-change="identityChanged"
      />
      <RecipientFields :model-value="draft.recipients" @update:model-value="update({ recipients: $event })" />
      <label class="v2-compose-subject"><span>主题</span><input :value="draft.subject" @input="update({ subject: ($event.target as HTMLInputElement).value })" /></label>
      <ComposeEditor
        :model-value="draft.body_html"
        :text-value="draft.body_text"
        @update:model-value="update({ body_html: $event })"
        @update:text-value="update({ body_text: $event })"
      />
      <DraftAttachments :draft-id="draft.id" :attachments="draft.attachments" @uploaded="addAttachment" @removed="removeAttachment" />
      <button type="button" @click="showServerPicker = true">从授权服务器路径添加</button>
      <p v-if="sendStatus" role="status">{{ sendStatus }}</p>
    </form>

    <ServerPathPicker v-if="showServerPicker && draft" :draft-id="draft.id" @imported="addAttachment" @close="showServerPicker = false" />
    <ScheduleSendDialog v-if="showSchedule" @schedule="send" @close="showSchedule = false" />
    <DraftConflictDialog
      v-if="controller?.conflict"
      :local="controller.conflict.local"
      :remote="controller.conflict.remote"
      @resolve="controller.resolveConflict($event)"
    />
  </main>
</template>

<style scoped>
.v2-compose-page { min-height: 100dvh; display: grid; align-content: start; background: var(--v2-surface); }
.v2-compose-page__header { position: sticky; z-index: 8; top: 0; display: flex; justify-content: space-between; gap: var(--v2-space-3); padding: var(--v2-space-4); border-bottom: 1px solid var(--v2-border); background: var(--v2-surface); }
.v2-compose-page__header h1, .v2-compose-page__header p { margin: 0; }
.v2-compose-page__actions { display: flex; align-items: center; gap: var(--v2-space-2); flex-wrap: wrap; }
.v2-compose-form { display: grid; gap: var(--v2-space-4); padding: var(--v2-space-4); }
.v2-compose-subject, .v2-identity-selector, .v2-recipient-fields label { display: grid; gap: var(--v2-space-1); }
.v2-compose-form input, .v2-compose-form select, .v2-compose-form textarea { min-height: var(--v2-control-height); padding: var(--v2-space-2); border: 1px solid var(--v2-border); border-radius: var(--v2-radius-sm); background: var(--v2-bg); color: var(--v2-text); }
@media (max-width: 767px) { .v2-compose-page__header { align-items: flex-start; flex-direction: column; padding-top: calc(var(--v2-space-4) + env(safe-area-inset-top)); } }
</style>

<template>
  <PageFrame template="management" class="unified-page ui-page">
    <template #header>
      <PageHeader title="聚合收件箱" description="统一查看所选邮箱的收件箱邮件。">
        <template #actions>
          <button class="btn btn-secondary" type="button" @click="settingsOpen = !settingsOpen">选择邮箱</button>
          <button class="btn btn-secondary" type="button" :disabled="loading" @click="loadMessages">刷新</button>
          <button
            class="btn btn-primary"
            type="button"
            :disabled="loading || !selectedAccountIds.length || unreadTotal === 0"
            @click="markAllRead"
          >
            全部已读
          </button>
        </template>
      </PageHeader>
    </template>

    <div class="management-stack unified-stack">

    <div v-if="settingsOpen" class="settings-card">
      <div class="settings-title">参与聚合的邮箱</div>
      <label v-for="account in availableAccounts" :key="account.id" class="account-option">
        <input v-model="selectedAccountIds" type="checkbox" :value="account.id" />
        <span>{{ account.email }}</span>
        <small>{{ providerName(account.provider) }}</small>
      </label>
      <div v-if="!availableAccounts.length" class="empty-inline">请先添加邮箱账号。</div>
      <div class="settings-actions">
        <button class="btn btn-primary" type="button" :disabled="savingSettings" @click="saveSettings">
          {{ savingSettings ? '保存中…' : '保存选择' }}
        </button>
      </div>
    </div>

    <div class="filter-bar">
      <select v-model="accountFilter" class="select" @change="resetAndLoad">
        <option value="">全部邮箱</option>
        <option v-for="account in selectedAccounts" :key="account.id" :value="account.id">
          {{ account.email }}
        </option>
      </select>
      <button class="filter-chip" :class="{ active: readFilter === '' && !attachmentOnly }" @click="setFilter('', false)">
        全部 {{ filterCounts.all }}
      </button>
      <button class="filter-chip" :class="{ active: readFilter === 'unread' }" @click="setFilter('unread', false)">
        未读 {{ filterCounts.unread }}
      </button>
      <button class="filter-chip" :class="{ active: readFilter === 'read' }" @click="setFilter('read', false)">
        已读 {{ filterCounts.read }}
      </button>
      <button class="filter-chip" :class="{ active: attachmentOnly }" @click="setFilter('', true)">
        有附件 {{ filterCounts.attachments }}
      </button>
      <span class="summary">共 {{ total }} 封，未读 {{ unreadTotal }} 封</span>
    </div>

    <UiLoadingState v-if="loading" panel label="正在加载聚合邮件…" />
    <UiEmptyState
      v-else-if="loadError"
      panel
      title="聚合收件箱加载失败"
      :description="loadError"
    >
      <button class="btn btn-secondary" type="button" @click="loadMessages">重试</button>
    </UiEmptyState>
    <UiEmptyState
      v-else-if="noAccounts"
      panel
      title="尚未选择聚合邮箱"
      description="点击“选择邮箱”勾选需要聚合的账号。"
    />
    <UiEmptyState
      v-else-if="!messages.length"
      panel
      title="没有匹配的邮件"
      description="调整邮箱或阅读状态筛选后再试。"
    />

    <div v-else class="message-list">
      <button
        v-for="message in messages"
        :key="`${message.account_id}:${message.folder || 'INBOX'}:${message.id}`"
        class="message-row"
        :class="{ unread: !message.is_read }"
        type="button"
        @click="openMessage(message)"
      >
        <span class="read-dot" aria-hidden="true"></span>
        <span class="sender" :title="message.from_addr">{{ displayAddress(message.from_addr) }}</span>
        <span class="message-main">
          <strong>{{ message.subject || '（无主题）' }}</strong>
          <small>{{ accountLabel(message) }}</small>
        </span>
        <span v-if="message.has_attachments" class="attachment" title="包含附件">📎</span>
        <time>{{ formatDate(message.date) }}</time>
      </button>
    </div>

    <footer v-if="total > pageSize" class="pagination">
      <button class="btn btn-secondary" type="button" :disabled="page <= 1 || loading" @click="changePage(page - 1)">上一页</button>
      <span>第 {{ page }} / {{ totalPages }} 页</span>
      <button class="btn btn-secondary" type="button" :disabled="page >= totalPages || loading" @click="changePage(page + 1)">下一页</button>
    </footer>
    </div>
  </PageFrame>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import PageFrame from '../components/layout/PageFrame.vue';
import PageHeader from '../components/layout/PageHeader.vue';
import UiEmptyState from '../components/ui/UiEmptyState.vue';
import UiLoadingState from '../components/ui/UiLoadingState.vue';
import { useMailStore } from '../stores/mail';
import { useUIStore } from '../stores/ui';
import type { Message } from '../types/mail';
import api from '../utils/api';
import { providerName } from '../utils/provider';

interface UnifiedAccount {
  id: string;
  email: string;
  provider: string;
  selected?: boolean;
}

interface UnifiedMessage extends Message {
  account_email?: string;
  account_provider?: string;
}

const mailStore = useMailStore();
const ui = useUIStore();
const settingsOpen = ref(false);
const savingSettings = ref(false);
const loading = ref(false);
const loadError = ref('');
const noAccounts = ref(false);
const availableAccounts = ref<UnifiedAccount[]>([]);
const selectedAccountIds = ref<string[]>([]);
const messages = ref<UnifiedMessage[]>([]);
const page = ref(1);
const pageSize = 40;
const total = ref(0);
const unreadTotal = ref(0);
const accountFilter = ref('');
const readFilter = ref('');
const attachmentOnly = ref(false);
const filterCounts = ref({ all: 0, unread: 0, read: 0, attachments: 0 });

const selectedAccounts = computed(() => availableAccounts.value.filter((account) => selectedAccountIds.value.includes(account.id)));
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)));

async function loadSettings() {
  const data = await api.get('/settings/unified') as any;
  availableAccounts.value = data.accounts || [];
  selectedAccountIds.value = Array.isArray(data.account_ids) ? data.account_ids : [];
}

async function saveSettings() {
  savingSettings.value = true;
  try {
    await api.put('/settings/unified', { account_ids: selectedAccountIds.value });
    if (accountFilter.value && !selectedAccountIds.value.includes(accountFilter.value)) {
      accountFilter.value = '';
    }
    settingsOpen.value = false;
    page.value = 1;
    await loadMessages();
    ui.success('聚合邮箱已保存');
  } catch (error: any) {
    ui.error(error?.error || error?.message || '保存失败');
  } finally {
    savingSettings.value = false;
  }
}

async function loadMessages() {
  loading.value = true;
  loadError.value = '';
  try {
    const params: Record<string, any> = {
      page: page.value,
      page_size: pageSize,
      read_filter: readFilter.value,
      attachment_filter: attachmentOnly.value,
    };
    if (accountFilter.value) params.account_filter = accountFilter.value;
    const data = await api.get('/messages/unified', { params }) as any;
    messages.value = data.messages || [];
    total.value = Number(data.total || 0);
    unreadTotal.value = Number(data.unread_total || 0);
    noAccounts.value = Boolean(data.no_accounts);
    filterCounts.value = {
      all: Number(data.filter_counts?.all || 0),
      unread: Number(data.filter_counts?.unread || 0),
      read: Number(data.filter_counts?.read || 0),
      attachments: Number(data.filter_counts?.attachments || 0),
    };
  } catch (error: any) {
    loadError.value = error?.error || error?.message || '加载聚合收件箱失败';
  } finally {
    loading.value = false;
  }
}

async function markAllRead() {
  const confirmed = await ui.showConfirm({
    title: '全部标记为已读',
    message: `确定将 ${selectedAccountIds.value.length} 个邮箱的收件箱全部标记为已读吗？`,
    confirmText: '全部已读',
  });
  if (!confirmed) return;
  try {
    const data = await api.post('/messages/mark-all-read', {
      account_ids: selectedAccountIds.value,
      folder: 'INBOX',
    }) as any;
    ui.success(`已标记 ${Number(data.total_marked || 0)} 封邮件`);
    await loadMessages();
    await mailStore.loadFolderCounts();
  } catch (error: any) {
    ui.error(error?.error || error?.message || '标记失败');
  }
}

function setFilter(read: string, attachments: boolean) {
  readFilter.value = read;
  attachmentOnly.value = attachments;
  resetAndLoad();
}

function resetAndLoad() {
  page.value = 1;
  loadMessages();
}

function changePage(nextPage: number) {
  page.value = nextPage;
  loadMessages();
}

function openMessage(message: UnifiedMessage) {
  if (!message.account_id) return;
  mailStore.setAccount(message.account_id);
  mailStore.setFolder(message.folder || 'INBOX');
  sessionStorage.setItem('flymail_pending_message', JSON.stringify({
    account_id: message.account_id,
    folder: message.folder || 'INBOX',
    id: message.id,
    uid: message.uid,
  }));
  window.dispatchEvent(new CustomEvent('flymail-navigate', { detail: 'mail' }));
}

function displayAddress(value: string) {
  const text = value || '未知发件人';
  const match = text.match(/^\s*"?([^"<]+)"?\s*</);
  return match?.[1]?.trim() || text;
}

function accountLabel(message: UnifiedMessage) {
  const account = availableAccounts.value.find((item) => item.id === message.account_id);
  return message.account_email || account?.email || providerName(message.account_provider || account?.provider || '');
}

function formatDate(value: string) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const now = new Date();
  if (date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  }
  return date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}

onMounted(async () => {
  try {
    await loadSettings();
    await loadMessages();
    if (!selectedAccountIds.value.length) settingsOpen.value = true;
  } catch (error: any) {
    loadError.value = error?.error || error?.message || '初始化聚合收件箱失败';
  }
});
</script>

<style scoped>
.unified-page { background: var(--ui-canvas); }
.unified-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
.unified-header h2 { margin: 0 0 6px; font-size: 24px; }
.unified-header p { margin: 0; color: var(--ui-text-3); }
.header-actions, .settings-actions, .pagination { display: flex; align-items: center; gap: 10px; }
.settings-card, .filter-bar, .message-list { background: var(--ui-surface-1); border: 1px solid var(--ui-border); border-radius: var(--ui-radius-md); }
.settings-card { padding: 18px; margin-bottom: 16px; }
.settings-title { font-weight: 700; margin-bottom: 10px; }
.account-option { display: grid; grid-template-columns: 20px minmax(0, 1fr) auto; gap: 10px; align-items: center; padding: 9px 0; }
.account-option small { color: var(--ui-text-3); }
.settings-actions { justify-content: flex-end; margin-top: 12px; }
.empty-inline { color: var(--ui-text-3); padding: 10px 0; }
.filter-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 12px; margin-bottom: 12px; }
.select { min-width: 180px; border: 1px solid var(--ui-border-strong); border-radius: 8px; padding: 8px 10px; background: var(--ui-surface-1); color: var(--ui-text-1); }
.filter-chip { border: 1px solid var(--ui-border-strong); border-radius: 999px; padding: 7px 12px; background: transparent; color: var(--ui-text-2); cursor: pointer; }
.filter-chip.active { border-color: var(--ui-accent); background: var(--ui-fill-selected); color: var(--ui-accent); }
.summary { margin-left: auto; color: var(--ui-text-3); font-size: 13px; }
.message-list { overflow: hidden; }
.message-row { width: 100%; display: grid; grid-template-columns: 12px minmax(130px, 220px) minmax(0, 1fr) 24px 72px; align-items: center; gap: 12px; padding: 13px 16px; border: 0; border-bottom: 1px solid var(--ui-border); background: var(--ui-surface-1); color: var(--ui-text-1); text-align: left; cursor: pointer; }
.message-row:last-child { border-bottom: 0; }
.message-row:hover { background: var(--ui-fill-hover); }
.message-row.unread { background: var(--ui-fill-selected); }
.read-dot { width: 7px; height: 7px; border-radius: 50%; background: transparent; }
.unread .read-dot { background: var(--ui-accent); }
.sender { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.unread .sender, .unread .message-main strong { font-weight: 700; }
.message-main { min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.message-main strong, .message-main small { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.message-main small { color: var(--ui-text-3); }
.attachment { text-align: center; }
time { color: var(--ui-text-3); font-size: 12px; text-align: right; }
.pagination { justify-content: center; margin-top: 16px; }
@media (max-width: 760px) {
  .unified-header { flex-direction: column; }
  .header-actions { width: 100%; flex-wrap: wrap; }
  .message-row { grid-template-columns: 8px minmax(0, 1fr) 58px; gap: 8px; padding: 12px; }
  .sender { grid-column: 2; }
  .message-main { grid-column: 2; }
  .attachment { display: none; }
  time { grid-column: 3; grid-row: 1; }
  .message-main small { display: none; }
  .summary { width: 100%; margin-left: 0; }
}
</style>

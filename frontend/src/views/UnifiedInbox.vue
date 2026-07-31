<template>
  <PageFrame template="management" width="fluid" class="unified-page ui-page">
    <template #header>
      <PageHeader title="聚合收件箱" description="统一查看所选邮箱的收件箱邮件。">
        <template #actions>
          <UiButton variant="secondary" @click="settingsOpen = !settingsOpen">选择邮箱</UiButton>
          <UiButton variant="secondary" :loading="loading" @click="loadMessages">刷新</UiButton>
          <UiButton
            variant="primary"
            :disabled="loading || !selectedAccountIds.length || unreadTotal === 0"
            @click="markAllRead"
          >
            全部已读
          </UiButton>
        </template>
      </PageHeader>
    </template>

    <div class="management-stack unified-stack">
      <UiCard v-if="settingsOpen" class="unified-account-card" padding="lg">
        <div class="unified-account-layout">
          <section class="ui-section">
            <div class="ui-section__header">
              <div class="ui-section__copy">
                <h2>参与聚合的邮箱</h2>
                <p>选择需要统一展示收件箱邮件的账号。</p>
              </div>
              <UiBadge tone="accent" size="md">已选 {{ selectedAccountIds.length }}</UiBadge>
            </div>

            <div v-if="availableAccounts.length" class="account-options">
              <label v-for="account in availableAccounts" :key="account.id" class="account-option ui-checkbox">
                <input v-model="selectedAccountIds" type="checkbox" :value="account.id" />
                <span class="account-option__copy">
                  <strong>{{ account.email }}</strong>
                  <small>收件箱将参与跨账号聚合</small>
                </span>
                <UiBadge>{{ providerName(account.provider) }}</UiBadge>
              </label>
            </div>
            <UiEmptyState
              v-else
              compact
              title="还没有邮箱账号"
              description="请先在账号管理中添加一个可用邮箱。"
            />
          </section>

          <aside class="selection-summary">
            <div>
              <small>当前聚合范围</small>
              <strong>{{ selectedAccountIds.length }} 个邮箱</strong>
              <p>保存后将重新加载聚合邮件和统计数据。</p>
            </div>
            <UiButton variant="primary" :loading="savingSettings" @click="saveSettings">保存选择</UiButton>
          </aside>
        </div>
      </UiCard>

      <UiCard class="unified-filter-card" padding="sm">
        <div class="unified-filter-row">
          <select v-model="accountFilter" class="ui-select account-filter" aria-label="筛选邮箱" @change="resetAndLoad">
            <option value="">全部邮箱</option>
            <option v-for="account in selectedAccounts" :key="account.id" :value="account.id">
              {{ account.email }}
            </option>
          </select>
          <UiSegmentedControl
            v-model="activeFilter"
            class="unified-filter-segments"
            label="筛选聚合邮件"
            :options="filterOptions"
          />
          <div class="unified-summary">
            <UiBadge size="md">共 {{ total }} 封</UiBadge>
            <UiBadge tone="accent" size="md">未读 {{ unreadTotal }} 封</UiBadge>
          </div>
        </div>
      </UiCard>

      <UiCard class="unified-message-card" padding="none">
        <UiLoadingState v-if="loading" label="正在加载聚合邮件…" />
        <UiEmptyState
          v-else-if="loadError"
          title="聚合收件箱加载失败"
          :description="loadError"
        >
          <UiButton variant="secondary" @click="loadMessages">重试</UiButton>
        </UiEmptyState>
        <UiEmptyState
          v-else-if="noAccounts"
          title="尚未选择聚合邮箱"
          description="点击“选择邮箱”勾选需要聚合的账号。"
        >
          <UiButton variant="primary" @click="settingsOpen = true">选择邮箱</UiButton>
        </UiEmptyState>
        <UiEmptyState
          v-else-if="!messages.length"
          title="没有匹配的邮件"
          description="调整邮箱或阅读状态筛选后再试。"
        />

        <div v-else class="message-list">
          <button
            v-for="message in messages"
            :key="`${message.account_id}:${message.folder || 'INBOX'}:${message.id}`"
            class="ui-list-row message-row"
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
            <UiBadge v-if="message.has_attachments" class="attachment" title="包含附件">附件</UiBadge>
            <time>{{ formatDate(message.date) }}</time>
          </button>
        </div>
      </UiCard>

      <footer v-if="total > pageSize" class="pagination">
        <UiButton variant="secondary" :disabled="page <= 1 || loading" @click="changePage(page - 1)">上一页</UiButton>
        <span>第 {{ page }} / {{ totalPages }} 页</span>
        <UiButton variant="secondary" :disabled="page >= totalPages || loading" @click="changePage(page + 1)">下一页</UiButton>
      </footer>
    </div>
  </PageFrame>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import PageFrame from '../components/layout/PageFrame.vue';
import PageHeader from '../components/layout/PageHeader.vue';
import UiBadge from '../components/ui/UiBadge.vue';
import UiButton from '../components/ui/UiButton.vue';
import UiCard from '../components/ui/UiCard.vue';
import UiEmptyState from '../components/ui/UiEmptyState.vue';
import UiLoadingState from '../components/ui/UiLoadingState.vue';
import UiSegmentedControl from '../components/ui/UiSegmentedControl.vue';
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
const activeFilter = computed({
  get: () => (attachmentOnly.value ? 'attachments' : readFilter.value || 'all'),
  set: (value: string) => {
    if (value === 'attachments') setFilter('', true);
    else setFilter(value === 'all' ? '' : value, false);
  },
});
const filterOptions = computed(() => [
  { value: 'all', label: '全部', count: filterCounts.value.all },
  { value: 'unread', label: '未读', count: filterCounts.value.unread },
  { value: 'read', label: '已读', count: filterCounts.value.read },
  { value: 'attachments', label: '有附件', count: filterCounts.value.attachments },
]);

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
.unified-stack {
  min-height: 0;
}

.unified-account-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(240px, 280px);
  gap: var(--ui-space-6);
  align-items: stretch;
}

.account-options {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: var(--ui-space-2);
}

.account-option {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr) auto;
  gap: var(--ui-space-3);
  padding: var(--ui-space-3);
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-md);
  background: var(--ui-surface-2);
}

.account-option:has(input:checked) {
  border-color: color-mix(in srgb, var(--ui-accent) 42%, var(--ui-border));
  background: var(--ui-fill-selected);
}

.account-option__copy {
  min-width: 0;
  display: grid;
  gap: var(--ui-space-1);
}

.account-option__copy strong,
.account-option__copy small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.account-option__copy small {
  color: var(--ui-text-3);
  font-size: var(--ui-text-xs);
}

.selection-summary {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: var(--ui-space-6);
  padding: var(--ui-space-5);
  border-radius: var(--ui-radius-md);
  background: var(--ui-surface-2);
}

.selection-summary > div {
  display: grid;
  gap: var(--ui-space-2);
}

.selection-summary small {
  color: var(--ui-text-3);
}

.selection-summary strong {
  font-size: 26px;
  line-height: 1;
  letter-spacing: -0.03em;
}

.selection-summary p {
  margin: 0;
  color: var(--ui-text-2);
  font-size: var(--ui-text-sm);
  line-height: 1.5;
}

.unified-filter-row {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: var(--ui-space-3);
}

.account-filter {
  width: auto;
  min-width: 190px;
}

.unified-summary {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: var(--ui-space-2);
}

.unified-message-card {
  min-height: 240px;
  overflow: hidden;
}

.message-list {
  min-width: 0;
}

.message-row {
  width: 100%;
  display: grid;
  grid-template-columns: 12px minmax(150px, 220px) minmax(0, 1fr) auto 72px;
  gap: var(--ui-space-3);
  border: 0;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.message-row.unread {
  background: var(--ui-fill-selected);
}

.read-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: transparent;
}

.unread .read-dot {
  background: var(--ui-accent);
}

.sender,
.message-main strong,
.message-main small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.unread .sender,
.unread .message-main strong {
  font-weight: 700;
}

.message-main {
  min-width: 0;
  display: grid;
  gap: var(--ui-space-1);
}

.message-main small {
  color: var(--ui-text-3);
}

time {
  color: var(--ui-text-3);
  font-size: var(--ui-text-xs);
  text-align: right;
}

.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--ui-space-3);
}

@media (max-width: 1180px) {
  .unified-account-layout {
    grid-template-columns: 1fr;
  }

  .selection-summary {
    flex-direction: row;
    align-items: center;
  }

  .unified-filter-row {
    flex-wrap: wrap;
  }

  .unified-summary {
    width: 100%;
    margin-left: 0;
  }
}

@media (max-width: 760px) {
  .account-options {
    grid-template-columns: 1fr;
  }

  .selection-summary {
    align-items: stretch;
    flex-direction: column;
  }

  .account-filter,
  .unified-filter-segments {
    width: 100%;
  }

  .unified-filter-segments :deep(.ui-segmented__item) {
    flex: 1;
    padding-inline: var(--ui-space-2);
  }

  .message-row {
    grid-template-columns: 8px minmax(0, 1fr) 58px;
    gap: var(--ui-space-2);
    padding: var(--ui-space-3);
  }

  .sender,
  .message-main {
    grid-column: 2;
  }

  .attachment {
    display: none;
  }

  time {
    grid-column: 3;
    grid-row: 1;
  }

  .message-main small {
    display: none;
  }
}
</style>

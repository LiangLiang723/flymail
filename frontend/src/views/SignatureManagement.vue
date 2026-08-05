<template>
  <PageFrame template="split" width="fluid" class="signature-management-page ui-page">
    <template #header>
      <div class="signature-page-header">
        <UiButton variant="secondary" size="sm" @click="requestBack">
          <template #leading>
            <AppIcon name="chevron-left" :size="16" />
          </template>
          返回
        </UiButton>
        <PageHeader title="签名管理" description="按邮箱管理新邮件和回复/转发默认签名。">
          <template #actions>
            <UiButton variant="primary" @click="requestCreate">
              <template #leading>
                <span aria-hidden="true">＋</span>
              </template>
              新建签名
            </UiButton>
          </template>
        </PageHeader>
      </div>
    </template>

    <div class="signature-workspace split-grid">
      <aside
        class="signature-list-pane ui-scroll-region ui-scroll-region--y"
        :class="{ 'mobile-hidden': signatureStore.mobileEditing }"
      >
        <div class="signature-list-toolbar">
          <label class="signature-search-box" for="signature-search">
            <AppIcon name="search" :size="16" />
            <input
              id="signature-search"
              v-model="signatureStore.search"
              class="ui-input"
              type="search"
              placeholder="搜索签名"
            />
          </label>
          <select v-model="signatureStore.accountFilter" class="ui-select" aria-label="按适用邮箱筛选">
            <option value="all">全部邮箱范围</option>
            <option value="">仅全部邮箱</option>
            <option v-for="account in mailStore.accounts" :key="account.id" :value="account.id">
              {{ account.email }}
            </option>
          </select>
        </div>

        <UiLoadingState v-if="signatureStore.loading" compact label="正在加载签名…" />

        <UiEmptyState
          v-else-if="signatureStore.filteredSignatures.length === 0"
          compact
          :title="signatureStore.search || signatureStore.accountFilter !== 'all' ? '没有匹配的签名' : '还没有签名'"
          :description="signatureStore.search || signatureStore.accountFilter !== 'all' ? '调整搜索关键词或邮箱筛选' : '创建签名后即可在写信时快速使用'"
        >
          <template #icon>
            <AppIcon name="signature" :size="30" />
          </template>
          <UiButton
            v-if="!signatureStore.search && signatureStore.accountFilter === 'all'"
            variant="primary"
            size="sm"
            @click="requestCreate"
          >
            创建第一个签名
          </UiButton>
        </UiEmptyState>

        <div v-else class="signature-list" role="listbox" aria-label="签名列表">
          <button
            v-for="signature in signatureStore.filteredSignatures"
            :key="signature.id"
            class="signature-list-item"
            :class="{ active: signature.id === signatureStore.selectedId }"
            type="button"
            role="option"
            :aria-selected="signature.id === signatureStore.selectedId"
            @click="requestSelect(signature.id)"
          >
            <span class="signature-list-copy">
              <strong>{{ signature.name }}</strong>
              <small>{{ accountLabel(signature.account_id) }}</small>
            </span>
            <span class="signature-list-badges">
              <UiBadge v-if="signature.is_default" tone="accent">新邮件默认</UiBadge>
              <UiBadge v-if="signature.is_reply_default" tone="success">回复/转发默认</UiBadge>
              <UiBadge v-if="isSignatureBodyEmpty(signature.content_html)" tone="warning">无正文</UiBadge>
            </span>
          </button>
        </div>
      </aside>

      <section
        class="signature-editor-pane ui-scroll-region ui-scroll-region--y"
        :class="{ 'mobile-show': signatureStore.mobileEditing }"
      >
        <div class="signature-mobile-toolbar">
          <UiButton variant="secondary" size="sm" @click="requestMobileList">
            <template #leading>
              <AppIcon name="chevron-left" :size="16" />
            </template>
            返回列表
          </UiButton>
          <UiButton variant="primary" size="sm" :loading="signatureStore.saving" @click="saveSignature">
            保存
          </UiButton>
        </div>

        <div class="signature-editor-content">
          <div class="signature-editor-heading">
            <div>
              <p class="signature-eyebrow">{{ signatureStore.draft.id === null ? '新签名' : '编辑签名' }}</p>
              <h2>{{ signatureStore.draft.name.trim() || '未命名签名' }}</h2>
              <p>{{ accountLabel(signatureStore.draft.account_id) }}</p>
            </div>
            <UiBadge v-if="signatureStore.hasUnsavedChanges" tone="warning">未保存</UiBadge>
          </div>

          <section v-if="showTemplatePicker" class="signature-template-section" aria-labelledby="signature-template-title">
            <div class="signature-section-heading">
              <div>
                <h3 id="signature-template-title">选择起点</h3>
                <p>模板只填充当前表单，保存后才会出现在写信菜单中。</p>
              </div>
            </div>
            <div class="signature-template-grid">
              <button
                v-for="template in signatureTemplates"
                :key="template.key"
                class="signature-template-card"
                type="button"
                @click="applyTemplate(template)"
              >
                <strong>{{ template.name }}</strong>
                <span v-html="template.preview"></span>
              </button>
            </div>
          </section>

          <div class="signature-form-grid">
            <label class="signature-field">
              <span>签名名称</span>
              <input
                v-model="signatureStore.draft.name"
                class="ui-input"
                type="text"
                maxlength="255"
                placeholder="例如：工作签名"
              />
            </label>

            <label class="signature-field">
              <span>适用邮箱</span>
              <select v-model="signatureStore.draft.account_id" class="ui-select">
                <option value="">全部邮箱</option>
                <option v-for="account in mailStore.accounts" :key="account.id" :value="account.id">
                  {{ account.email }}
                </option>
              </select>
            </label>
          </div>

          <div class="signature-default-card">
            <div class="signature-section-heading">
              <div>
                <h3>默认使用规则</h3>
                <p>同一邮箱范围内，每类默认签名只保留一个。</p>
              </div>
            </div>
            <div class="signature-default-options">
              <UiCheckbox v-model="signatureStore.draft.is_default" label="新邮件默认">
                新邮件默认
              </UiCheckbox>
              <UiCheckbox v-model="signatureStore.draft.is_reply_default" label="回复/转发默认">
                回复/转发默认
              </UiCheckbox>
            </div>
          </div>

          <div class="signature-body-section">
            <div class="signature-section-heading">
              <div>
                <h3>签名正文</h3>
                <p>支持字体、字号、颜色、链接、图片和表格，最多保存 10000 个字符。</p>
              </div>
              <UiBadge v-if="isSignatureBodyEmpty(signatureStore.draft.content_html)" tone="warning">该签名没有正文</UiBadge>
            </div>
            <TiptapEditor v-model="signatureStore.draft.content_html" class="signature-full-editor" />
          </div>
        </div>

        <div class="signature-action-bar">
          <div class="signature-action-bar__danger">
            <UiButton
              v-if="signatureStore.selectedSignature"
              variant="danger"
              :loading="signatureStore.deleting"
              @click="deleteSignature"
            >
              删除
            </UiButton>
          </div>
          <div class="signature-action-bar__main">
            <UiButton
              v-if="signatureStore.selectedSignature"
              variant="secondary"
              @click="duplicateSignature"
            >
              复制
            </UiButton>
            <UiButton
              variant="secondary"
              :disabled="!signatureStore.hasUnsavedChanges"
              @click="cancelChanges"
            >
              取消更改
            </UiButton>
            <UiButton variant="primary" :loading="signatureStore.saving" @click="saveSignature">
              保存签名
            </UiButton>
          </div>
        </div>
      </section>
    </div>
  </PageFrame>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import AppIcon from '../components/AppIcon.vue';
import TiptapEditor from '../components/TiptapEditor.vue';
import PageFrame from '../components/layout/PageFrame.vue';
import PageHeader from '../components/layout/PageHeader.vue';
import UiBadge from '../components/ui/UiBadge.vue';
import UiButton from '../components/ui/UiButton.vue';
import UiCheckbox from '../components/ui/UiCheckbox.vue';
import UiEmptyState from '../components/ui/UiEmptyState.vue';
import UiLoadingState from '../components/ui/UiLoadingState.vue';
import { useMailStore } from '../stores/mail';
import { useSignatureStore } from '../stores/signatures';
import { useUIStore } from '../stores/ui';

interface SignatureStartingTemplate {
  key: 'blank' | 'business' | 'contact' | 'brand' | 'minimal';
  name: string;
  content_html: string;
  preview: string;
}

const signatureTemplates: SignatureStartingTemplate[] = [
  { key: 'blank', name: '空白签名', content_html: '<p><br></p>', preview: '<p>从空白内容开始</p>' },
  { key: 'business', name: '简洁商务', content_html: '<p><strong>姓名</strong></p><p>职位 · 公司</p><p>name@example.com · 138 0000 0000</p>', preview: '<p><strong>姓名</strong><br>职位 · 公司</p>' },
  { key: 'contact', name: '联系方式', content_html: '<p>姓名</p><p><a href="mailto:name@example.com">name@example.com</a> · 138 0000 0000</p>', preview: '<p>姓名<br>邮箱 · 电话</p>' },
  { key: 'brand', name: '品牌卡片', content_html: '<div style="border-left:3px solid #6c63ff;padding-left:12px"><p><strong>姓名</strong></p><p>公司名称 · 职位</p><p>品牌标语</p></div>', preview: '<p><strong>姓名</strong><br>公司 · 品牌标语</p>' },
  { key: 'minimal', name: '极简落款', content_html: '<p>— 姓名</p>', preview: '<p>— 姓名</p>' },
];

const emit = defineEmits<{ back: [] }>();
const mailStore = useMailStore();
const signatureStore = useSignatureStore();
const uiStore = useUIStore();
const isMobile = ref(window.innerWidth <= 768);

const showTemplatePicker = computed(() => (
  signatureStore.draft.id === null
  && !signatureStore.draft.name.trim()
));

function accountLabel(accountId: string) {
  if (!accountId) return '全部邮箱';
  return mailStore.accounts.find((account) => account.id === accountId)?.email || '指定邮箱';
}

function isSignatureBodyEmpty(html: string) {
  return String(html || '')
    .replace(/<br\s*\/?>/gi, '')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;|\s/gi, '')
    .length === 0;
}

async function confirmDiscardChanges(): Promise<boolean> {
  if (!signatureStore.hasUnsavedChanges) return true;
  const confirmed = await uiStore.showConfirm({
    title: '放弃未保存的签名更改？',
    message: '当前签名尚未保存，继续后这些更改将丢失。',
    confirmText: '放弃更改',
    danger: true,
  });
  if (confirmed) signatureStore.discardDraft();
  return confirmed;
}

async function requestSelect(id: number) {
  if (signatureStore.selectedId === id) {
    signatureStore.mobileEditing = true;
    return;
  }
  if (!await confirmDiscardChanges()) return;
  signatureStore.beginEdit(id);
}

async function requestCreate() {
  if (!await confirmDiscardChanges()) return;
  signatureStore.beginCreate();
}

async function requestBack() {
  if (!await confirmDiscardChanges()) return;
  emit('back');
}

async function requestMobileList() {
  if (!isMobile.value) return;
  if (!await confirmDiscardChanges()) return;
  signatureStore.mobileEditing = false;
}

function applyTemplate(template: SignatureStartingTemplate) {
  if (signatureStore.draft.id !== null) signatureStore.beginCreate(signatureStore.draft.account_id);
  signatureStore.draft.name = template.key === 'blank' ? '' : template.name;
  signatureStore.draft.content_html = template.content_html;
}

async function saveSignature() {
  try {
    await signatureStore.saveDraft();
  } catch (error: any) {
    uiStore.error(error?.message || '保存签名失败');
  }
}

async function duplicateSignature() {
  const selected = signatureStore.selectedSignature;
  if (!selected || !await confirmDiscardChanges()) return;
  signatureStore.beginDuplicate(selected.id);
}

function cancelChanges() {
  signatureStore.discardDraft();
}

async function deleteSignature() {
  const selected = signatureStore.selectedSignature;
  if (!selected) return;
  const confirmed = await uiStore.showConfirm({
    title: '删除签名',
    message: `确定删除“${selected.name}”吗？`,
    confirmText: '删除',
    danger: true,
  });
  if (!confirmed) return;
  try {
    await signatureStore.deleteSelected();
  } catch (error: any) {
    uiStore.error(error?.message || '删除签名失败');
  }
}

function handleBeforeUnload(event: BeforeUnloadEvent) {
  if (!signatureStore.hasUnsavedChanges) return;
  event.preventDefault();
  event.returnValue = '';
}

function handleResize() {
  isMobile.value = window.innerWidth <= 768;
  if (!isMobile.value) signatureStore.mobileEditing = false;
}

onMounted(async () => {
  window.addEventListener('beforeunload', handleBeforeUnload);
  window.addEventListener('resize', handleResize);
  try {
    await signatureStore.ensureLoaded();
  } catch (error: any) {
    uiStore.error(error?.message || '加载签名失败');
  }
});

onUnmounted(() => {
  window.removeEventListener('beforeunload', handleBeforeUnload);
  window.removeEventListener('resize', handleResize);
});
</script>

<style scoped>
.signature-page-header {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: center;
  gap: 14px;
}

.signature-workspace {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  background: var(--ui-surface-1);
}

.signature-list-pane {
  min-width: 0;
  border-right: 1px solid var(--ui-border);
  background: var(--ui-surface-2);
}

.signature-list-toolbar {
  position: sticky;
  top: 0;
  z-index: 2;
  display: grid;
  gap: 10px;
  padding: 16px;
  border-bottom: 1px solid var(--ui-border);
  background: color-mix(in srgb, var(--ui-surface-2) 92%, transparent);
  backdrop-filter: blur(16px);
}

.signature-search-box {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--ui-text-3);
}

.signature-search-box .ui-input {
  min-width: 0;
  flex: 1;
}

.signature-list {
  display: grid;
  gap: 8px;
  padding: 12px;
}

.signature-list-item {
  width: 100%;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
  gap: 10px;
  padding: 13px 14px;
  border: 1px solid transparent;
  border-radius: var(--ui-radius-md);
  background: transparent;
  color: var(--ui-text-1);
  text-align: left;
  cursor: pointer;
}

.signature-list-item:hover {
  background: var(--ui-fill-hover);
}

.signature-list-item.active {
  border-color: color-mix(in srgb, var(--ui-accent) 36%, var(--ui-border));
  background: var(--ui-fill-selected);
  box-shadow: 0 0 0 3px var(--ui-focus-ring);
}

.signature-list-copy {
  min-width: 0;
  display: grid;
  gap: 5px;
}

.signature-list-copy strong,
.signature-list-copy small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.signature-list-copy strong {
  font-size: 14px;
}

.signature-list-copy small {
  color: var(--ui-text-3);
  font-size: 12px;
}

.signature-list-badges {
  max-width: 128px;
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 5px;
}

.signature-editor-pane {
  min-width: 0;
  display: grid;
  grid-template-rows: minmax(0, 1fr) auto;
  background: var(--ui-surface-1);
}

.signature-editor-content {
  width: min(100%, 1040px);
  margin: 0 auto;
  display: grid;
  gap: 20px;
  padding: 28px clamp(20px, 4vw, 48px) 32px;
}

.signature-editor-heading,
.signature-section-heading,
.signature-action-bar,
.signature-default-options {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.signature-editor-heading h2,
.signature-section-heading h3 {
  margin: 0;
  color: var(--ui-text-1);
}

.signature-editor-heading h2 {
  font-size: clamp(22px, 3vw, 30px);
}

.signature-editor-heading p,
.signature-section-heading p {
  margin: 5px 0 0;
  color: var(--ui-text-3);
  font-size: 13px;
}

.signature-eyebrow {
  margin: 0 0 6px !important;
  color: var(--ui-accent) !important;
  font-size: 11px !important;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.signature-template-section,
.signature-default-card,
.signature-body-section {
  display: grid;
  gap: 14px;
  padding: 18px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-lg);
  background: var(--ui-surface-2);
}

.signature-template-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(118px, 1fr));
  gap: 10px;
}

.signature-template-card {
  min-width: 0;
  min-height: 110px;
  display: grid;
  align-content: space-between;
  gap: 10px;
  padding: 13px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-md);
  background: var(--ui-surface-1);
  color: var(--ui-text-1);
  text-align: left;
  cursor: pointer;
}

.signature-template-card:hover {
  border-color: var(--ui-accent);
  background: var(--ui-fill-selected);
}

.signature-template-card span {
  max-height: 54px;
  overflow: hidden;
  color: var(--ui-text-3);
  font-size: 11px;
  line-height: 1.35;
}

.signature-form-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(220px, 0.8fr);
  gap: 16px;
}

.signature-field {
  display: grid;
  gap: 8px;
}

.signature-field > span {
  color: var(--ui-text-2);
  font-size: 13px;
  font-weight: 650;
}

.signature-default-options {
  justify-content: flex-start;
  flex-wrap: wrap;
}

.signature-full-editor {
  min-width: 0;
}

.signature-action-bar {
  position: sticky;
  bottom: 0;
  padding: 14px 20px;
  border-top: 1px solid var(--ui-border);
  background: color-mix(in srgb, var(--ui-surface-1) 92%, transparent);
  backdrop-filter: blur(18px);
}

.signature-action-bar__main {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 10px;
}

.signature-mobile-toolbar {
  display: none;
}

@media (max-width: 1180px) {
  .signature-template-grid {
    grid-template-columns: repeat(3, minmax(130px, 1fr));
  }
}

@media (max-width: 768px) {
  .signature-page-header {
    grid-template-columns: 1fr;
    gap: 10px;
    padding: 12px 14px 0;
  }

  .signature-workspace {
    grid-template-columns: 1fr;
  }

  .signature-list-pane,
  .signature-editor-pane {
    grid-column: 1;
    grid-row: 1;
    border: 0;
  }

  .signature-list-pane.mobile-hidden,
  .signature-editor-pane:not(.mobile-show) {
    display: none;
  }

  .signature-editor-pane.mobile-show {
    display: grid;
  }

  .signature-mobile-toolbar {
    position: sticky;
    top: 0;
    z-index: 4;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 10px 14px;
    border-bottom: 1px solid var(--ui-border);
    background: color-mix(in srgb, var(--ui-surface-1) 94%, transparent);
    backdrop-filter: blur(18px);
  }

  .signature-editor-content {
    padding: 20px 14px 96px;
  }

  .signature-form-grid,
  .signature-template-grid {
    grid-template-columns: 1fr;
  }

  .signature-template-card {
    min-height: 88px;
  }

  .signature-editor-heading,
  .signature-section-heading,
  .signature-action-bar {
    align-items: flex-start;
    flex-direction: column;
  }

  .signature-action-bar {
    display: none;
  }

  .signature-default-options {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>

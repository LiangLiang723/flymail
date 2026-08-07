<template>
  <PageFrame template="workspace" width="fluid" class="compose-page ui-page" @dragover.prevent="isDragging = true" @dragleave.prevent="isDragging = false" @drop.prevent="handleDrop">
    <!-- 拖拽上传遮罩 -->
    <div v-if="isDragging" class="drop-overlay">
      <div class="drop-hint">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="var(--accent-blue)" stroke-width="1.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
        <span>释放以添加附件</span>
      </div>
    </div>

    <!-- 顶部工具栏 -->
    <div class="compose-toolbar">
      <UiButton variant="primary" :loading="sending" :disabled="attachmentOverLimit" @click="sendMail">
        <template #leading>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
        </template>
        发送
      </UiButton>
      <UiButton variant="secondary" @click="showScheduleModal = true; initScheduleTime()">
        <template #leading>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
        </template>
        定时
      </UiButton>
      <UiButton variant="secondary" :loading="savingDraft" @click="saveDraft">
        <template #leading>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
        </template>
        草稿
      </UiButton>

      <!-- 签名快速选择 -->
      <div class="toolbar-dropdown sig-dropdown">
        <button class="toolbar-btn" title="签名" type="button" @click="showSignaturePanel = !showSignaturePanel">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/></svg>
          <span>签名</span>
        </button>
        <div v-if="showSignaturePanel" class="sig-panel">
          <div class="sig-current-summary">
            <span>当前签名</span>
            <strong>{{ activeSignatureName }}</strong>
          </div>
          <button
            class="sig-quick-item"
            :class="{ active: activeSignatureId === null }"
            type="button"
            @click="selectSignature(null)"
          >
            <span class="sig-quick-copy"><strong>无签名</strong><small>移除当前签名块</small></span>
            <span v-if="activeSignatureId === null" class="sig-quick-check">✓</span>
          </button>
          <button
            v-for="sig in availableUserSigs"
            :key="sig.id"
            class="sig-quick-item"
            :class="{ active: activeSignatureId === sig.id }"
            type="button"
            @click="selectSignature(sig)"
          >
            <span class="sig-quick-copy">
              <strong>{{ sig.name }}</strong>
              <small>{{ signatureAccountLabel(sig.account_id) }}</small>
            </span>
            <span class="sig-quick-badges">
              <span v-if="signatureDefaultLabel(sig)" class="sig-quick-badge">{{ signatureDefaultLabel(sig) }}</span>
              <span v-if="activeSignatureId === sig.id" class="sig-quick-check">✓</span>
            </span>
          </button>
          <div v-if="availableUserSigs.length === 0" class="sig-empty-hint">当前邮箱暂无可用签名</div>
          <div class="sig-panel-divider"></div>
          <button class="sig-manage-button" type="button" @click="openSignatureManager">
            管理签名
            <span aria-hidden="true">→</span>
          </button>
        </div>
      </div>

      <div class="toolbar-spacer"></div>
      <button class="toolbar-btn danger" @click="discardMail" title="关闭">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>

    <!-- 邮件表单 -->
    <div class="compose-form">
      <!-- 发件人 -->
      <div class="form-row">
        <label for="compose-from" class="compose-field-label">发件人</label>
        <div class="compose-field-control">
          <div class="compose-from-control">
            <select id="compose-from" v-model="fromAccountId" class="ui-select form-select">
              <option v-for="acc in accounts" :key="acc.id" :value="acc.id">{{ acc.email }}</option>
            </select>
            <span class="cc-links">
              <button v-if="!showCc" class="text-btn" @click="showCc = true">抄送</button>
              <button v-if="!showBcc" class="text-btn" @click="showBcc = true">密送</button>
            </span>
          </div>
        </div>
      </div>

      <!-- 收件人 -->
      <div class="form-row">
        <label class="compose-field-label">收件人</label>
        <div class="compose-field-control">
          <div class="tag-input">
            <span v-for="(addr, i) in toList" :key="'to'+i" class="tag">
              <span class="tag-label" :title="addr">{{ addr }}</span>
              <button class="tag-remove" type="button" :aria-label="`移除收件人 ${addr}`" @click="toList.splice(i, 1)">&times;</button>
            </span>
            <input v-model="toInput" type="text" inputmode="email" autocomplete="email" enterkeyhint="done" @input="toField.onInput" @keydown="handleRecipientKeydown('to', $event)" @keydown.comma.prevent="addRecipient('to')" @change="addRecipient('to')" @blur="closeRecipientSuggestions('to')" placeholder="输入姓名或邮箱" class="tag-input-field" />
            <div v-if="toField.showSuggestions.value" class="contact-suggestions">
              <button v-for="(item, index) in toField.suggestions.value" :key="`to-${item.contact_id}-${item.email}`" type="button" :class="{ active: index === toField.activeIndex.value }" @mousedown.prevent="chooseSuggestion('to', item)">
                <strong>{{ item.name || item.email }}</strong><small>{{ item.email }}</small>
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- 抄送（点击后显示） -->
      <div v-if="showCc" class="form-row">
        <label class="compose-field-label">抄送</label>
        <div class="compose-field-control">
          <div class="tag-input">
            <span v-for="(addr, i) in ccList" :key="'cc'+i" class="tag">
              <span class="tag-label" :title="addr">{{ addr }}</span>
              <button class="tag-remove" type="button" :aria-label="`移除抄送人 ${addr}`" @click="ccList.splice(i, 1)">&times;</button>
            </span>
            <input v-model="ccInput" type="text" inputmode="email" autocomplete="email" enterkeyhint="done" @input="ccField.onInput" @keydown="handleRecipientKeydown('cc', $event)" @keydown.comma.prevent="addRecipient('cc')" @change="addRecipient('cc')" @blur="closeRecipientSuggestions('cc')" placeholder="输入姓名或邮箱" class="tag-input-field" />
            <div v-if="ccField.showSuggestions.value" class="contact-suggestions">
              <button v-for="(item, index) in ccField.suggestions.value" :key="`cc-${item.contact_id}-${item.email}`" type="button" :class="{ active: index === ccField.activeIndex.value }" @mousedown.prevent="chooseSuggestion('cc', item)">
                <strong>{{ item.name || item.email }}</strong><small>{{ item.email }}</small>
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- 密送（点击后显示） -->
      <div v-if="showBcc" class="form-row">
        <label class="compose-field-label">密送</label>
        <div class="compose-field-control">
          <div class="tag-input">
            <span v-for="(addr, i) in bccList" :key="'bcc'+i" class="tag">
              <span class="tag-label" :title="addr">{{ addr }}</span>
              <button class="tag-remove" type="button" :aria-label="`移除密送人 ${addr}`" @click="bccList.splice(i, 1)">&times;</button>
            </span>
            <input v-model="bccInput" type="text" inputmode="email" autocomplete="email" enterkeyhint="done" @input="bccField.onInput" @keydown="handleRecipientKeydown('bcc', $event)" @keydown.comma.prevent="addRecipient('bcc')" @change="addRecipient('bcc')" @blur="closeRecipientSuggestions('bcc')" placeholder="输入姓名或邮箱" class="tag-input-field" />
            <div v-if="bccField.showSuggestions.value" class="contact-suggestions">
              <button v-for="(item, index) in bccField.suggestions.value" :key="`bcc-${item.contact_id}-${item.email}`" type="button" :class="{ active: index === bccField.activeIndex.value }" @mousedown.prevent="chooseSuggestion('bcc', item)">
                <strong>{{ item.name || item.email }}</strong><small>{{ item.email }}</small>
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- 主题 -->
      <UiField class="form-row" for-id="compose-subject">
        <span class="compose-field-label">主题</span>
        <div class="compose-field-control">
          <input id="compose-subject" v-model="subject" placeholder="邮件主题" class="ui-input form-input" />
        </div>
      </UiField>

      <!-- 富文本编辑器 -->
      <div class="editor-row">
        <TiptapEditor v-model="bodyHtml" ref="editorRef" />
      </div>

      <!-- 附件区域（在编辑器下方、表单底部） -->
      <div class="attachments-section">
        <div class="attachments-header">
          <div class="attachment-actions">
            <label class="upload-btn">
              <input type="file" multiple @change="handleFileSelect" class="hidden-input" />
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
              本机附件
            </label>
            <button class="upload-btn" type="button" @click="showNasPicker = true">从 NAS 添加</button>
          </div>
          <span v-if="attachments.length" class="attachments-count" :class="{ error: attachmentOverLimit }">
            {{ attachments.length }}个 · {{ formatSize(totalAttachmentBytes) }} / {{ attachmentLimitMb }}MB
          </span>
        </div>
        <div v-if="attachments.length" class="attachments-list">
          <div v-for="(att, i) in attachments" :key="i" class="attachment-item">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
            <span class="att-name">{{ att.filename }}</span>
            <span v-if="att.source === 'nas'" class="att-source">NAS</span>
            <span class="att-size">{{ formatSize(att.size) }}</span>
            <button class="att-remove" type="button" :aria-label="`移除附件 ${att.filename}`" @click="removeAttachment(i)">&times;</button>
          </div>
        </div>
      </div>
    </div>

    <NasPathPicker v-model="showNasPicker" mode="file" title="从 NAS 选择附件" @confirm="addNasAttachment" />

    <!-- 定时发送弹窗 -->
    <div v-if="showScheduleModal" class="modal-overlay" @click.self="showScheduleModal = false">
      <div class="modal-content schedule-modal">
        <div class="schedule-header">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--accent-blue)" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
          <h3>定时发送</h3>
        </div>
        <p class="modal-desc">选择发送时间，邮件将在指定时间自动发送</p>

        <!-- 日期时间选择卡片 -->
        <div class="schedule-card">
          <div class="schedule-card-row">
            <select v-model="scheduleYear" class="sc-select sc-select-wide">
              <option v-for="y in yearOptions" :key="y" :value="y">{{ y }}</option>
            </select>
            <span class="sc-sep">/</span>
            <select v-model="scheduleMonth" class="sc-select">
              <option v-for="m in 12" :key="m" :value="m">{{ String(m).padStart(2, '0') }}</option>
            </select>
            <span class="sc-sep">/</span>
            <select v-model="scheduleDay" class="sc-select">
              <option v-for="d in dayOptions" :key="d" :value="d">{{ String(d).padStart(2, '0') }}</option>
            </select>
            <span class="sc-gap"></span>
            <select v-model="scheduleHour" class="sc-select">
              <option v-for="h in 24" :key="h" :value="h - 1">{{ String(h - 1).padStart(2, '0') }}</option>
            </select>
            <span class="sc-sep">:</span>
            <select v-model="scheduleMinute" class="sc-select">
              <option v-for="m in 60" :key="m" :value="m - 1">{{ String(m - 1).padStart(2, '0') }}</option>
            </select>
          </div>
        </div>

        <!-- 预览条 -->
        <div class="schedule-preview-bar">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--accent-blue)" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
          <span>将于 <strong>{{ schedulePreview }}</strong> 自动发送</span>
        </div>

        <!-- 待发邮件列表（有待发任务时才显示） -->
        <div v-if="scheduledJobs.length > 0" class="scheduled-list">
          <div class="scheduled-list-title">待发邮件</div>
          <div v-for="job in scheduledJobs" :key="job.id" class="scheduled-item">
            <div class="scheduled-item-info">
              <span class="scheduled-item-subject">{{ job.kwargs?.subject || '(无主题)' }}</span>
              <span class="scheduled-item-time">{{ formatScheduleTime(job.next_run_time) }}</span>
            </div>
            <button class="scheduled-item-cancel" @click="cancelSchedule(job.id)" title="取消发送">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
          </div>
        </div>
        <div class="modal-actions">
          <button class="toolbar-btn" @click="showScheduleModal = false">取消</button>
          <button class="toolbar-btn primary" @click="scheduleMail" :disabled="!isScheduleValid">定时发送</button>
        </div>
      </div>
    </div>

    <!-- 定时发送成功弹窗 -->
    <div v-if="showScheduleSuccessModal" class="modal-overlay" @click.self="showScheduleSuccessModal = false">
      <div class="modal-content success-modal">
        <div class="success-icon">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#34C759" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
        </div>
        <h3>定时任务已创建</h3>
        <p class="success-desc">邮件将在 <strong>{{ scheduleSuccessTime }}</strong> 自动发送</p>
        <div class="modal-actions" style="justify-content: center;">
          <button class="toolbar-btn primary" @click="showScheduleSuccessModal = false">知道了</button>
        </div>
      </div>
    </div>

    <!-- 确认对话框 -->
    <div v-if="showConfirmDialog" class="modal-overlay" @click.self="showConfirmDialog = false">
      <div class="modal-content confirm-modal">
        <div class="confirm-icon">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="var(--text-secondary)" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        </div>
        <p class="confirm-text">{{ confirmMessage }}</p>
        <div class="modal-actions">
          <button class="toolbar-btn" @click="showConfirmDialog = false">取消</button>
          <button class="toolbar-btn danger" @click="confirmCallback(); showConfirmDialog = false">确认</button>
        </div>
      </div>
    </div>

    <!-- Toast 通知 -->
    <Transition name="toast">
      <div v-if="toast.visible" class="toast" :class="toast.type">
        <svg v-if="toast.type === 'success'" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
        <svg v-else-if="toast.type === 'error'" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
        <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        <span>{{ toast.message }}</span>
      </div>
    </Transition>
  </PageFrame>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, onMounted, watch } from 'vue';
import api from '../utils/api';
import { useMailStore, type ComposeWorkspaceSnapshot } from '../stores/mail';
import { useSignatureStore } from '../stores/signatures';
import NasPathPicker from '../components/NasPathPicker.vue';
import PageFrame from '../components/layout/PageFrame.vue';
import UiButton from '../components/ui/UiButton.vue';
import UiField from '../components/ui/UiField.vue';
import TiptapEditor from '../components/TiptapEditor.vue';
import { useContactAutocomplete } from '../composables/useContactAutocomplete';
import type { ContactSuggestion } from '../composables/useContacts';
import type { ComposeKind, SignatureTemplate } from '../types/signature';
import { resolveDefaultSignature } from '../utils/signature-management';

const emit = defineEmits<{
  discard: [];
  sent: [payload?: { account_id?: string }];
}>();

const mailStore = useMailStore();
const signatureStore = useSignatureStore();

// 表单数据
const fromAccountId = ref('');
const toList = ref<string[]>([]);
const ccList = ref<string[]>([]);
const bccList = ref<string[]>([]);
const subject = ref('');
const bodyHtml = ref('');
const draftMessageId = ref('');
const draftFolder = ref('');
const savedSnapshot = ref('');
const showCc = ref(false);
const showBcc = ref(false);

// 收件人输入
const toInput = ref('');
const ccInput = ref('');
const bccInput = ref('');
const { createField } = useContactAutocomplete();
const toField = createField(toInput);
const ccField = createField(ccInput);
const bccField = createField(bccInput);

// 附件
const attachments = ref<{ filename: string; size: number; path: string; source?: 'local' | 'nas' }[]>([]);
const showNasPicker = ref(false);
const isDragging = ref(false);

// 状态
const sending = ref(false);
const savingDraft = ref(false);
const showScheduleModal = ref(false);

// ---- Toast 通知系统 ----
const toast = ref({ visible: false, message: '', type: 'success' as 'success' | 'error' | 'info' });
let toastTimer: ReturnType<typeof setTimeout> | null = null;

/** 显示 Toast 通知（替代 alert） */
function showToast(message: string, type: 'success' | 'error' | 'info' = 'success') {
  if (toastTimer) clearTimeout(toastTimer);
  toast.value = { visible: true, message, type };
  toastTimer = setTimeout(() => { toast.value.visible = false; }, 2500);
}

// ---- 确认对话框（替代 confirm） ----
const showConfirmDialog = ref(false);
const confirmMessage = ref('');
const confirmCallback = ref(() => {});

// ---- 确认对话框（替代 confirm） ----
function showConfirm(message: string, callback: () => void) {
  confirmMessage.value = message;
  confirmCallback.value = callback;
  showConfirmDialog.value = true;
}

// ---- 定时发送：下拉框选择器 ----
const scheduleYear = ref(new Date().getFullYear());
const scheduleMonth = ref(new Date().getMonth() + 1);
const scheduleDay = ref(new Date().getDate());
const scheduleHour = ref(new Date().getHours());
const scheduleMinute = ref(new Date().getMinutes());

// ---- 定时发送：待发邮件列表 ----
const scheduledJobs = ref<any[]>([]);
const showScheduleSuccessModal = ref(false);
const scheduleSuccessTime = ref('');

/** 加载待执行的定时发送任务 */
async function loadScheduledJobs() {
  try {
    const data = await api.get('/messages/scheduled') as any;
    const allJobs = data?.jobs || [];
    // 只显示有待执行时间的任务（已执行的任务 next_run_time 为 null）
    scheduledJobs.value = allJobs.filter((j: any) => j.next_run_time);
    console.log('[定时发送] 加载待发列表:', scheduledJobs.value.length, '条', scheduledJobs.value);
  } catch (e) {
    console.warn('[定时发送] 加载待发列表失败:', e);
    scheduledJobs.value = [];
  }
}

/** 取消定时发送任务 */
async function cancelSchedule(jobId: string) {
  try {
    await api.delete(`/messages/scheduled/${jobId}`);
    scheduledJobs.value = scheduledJobs.value.filter((j: any) => j.id !== jobId);
  } catch { /* 忽略 */ }
}

/** 年份选项：当前年 ~ 后3年 */
const yearOptions = computed(() => {
  const now = new Date().getFullYear();
  return [now, now + 1, now + 2, now + 3];
});

/** 日期选项：根据年月动态计算天数 */
const dayOptions = computed(() => {
  const daysInMonth = new Date(scheduleYear.value, scheduleMonth.value, 0).getDate();
  return daysInMonth;
});

/** 定时时间是否有效（不早于当前时间） */
const isScheduleValid = computed(() => {
  const now = new Date();
  const scheduled = new Date(scheduleYear.value, scheduleMonth.value - 1, scheduleDay.value, scheduleHour.value, scheduleMinute.value);
  return scheduled > now;
});

/** 定时时间预览文字 */
const schedulePreview = computed(() => {
  const y = scheduleYear.value;
  const m = String(scheduleMonth.value).padStart(2, '0');
  const d = String(scheduleDay.value).padStart(2, '0');
  const h = String(scheduleHour.value).padStart(2, '0');
  const min = String(scheduleMinute.value).padStart(2, '0');
  const str = `${y}-${m}-${d} ${h}:${min}`;
  return str;
});

/** 格式化定时任务执行时间（ISO 字符串 → 可读格式） */
function formatScheduleTime(isoStr: string): string {
  if (!isoStr) return '';
  try {
    const match = isoStr.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
    if (match) {
      return `${match[1]}-${match[2]}-${match[3]} ${match[4]}:${match[5]}`;
    }
    return isoStr;
  } catch { return isoStr; }
}

/** 打开定时弹窗时，初始化为当前时间 + 加载待发列表 */
function initScheduleTime() {
  const now = new Date();
  scheduleYear.value = now.getFullYear();
  scheduleMonth.value = now.getMonth() + 1;
  scheduleDay.value = now.getDate();
  scheduleHour.value = now.getHours();
  scheduleMinute.value = now.getMinutes();
  loadScheduledJobs();
}

// 账号列表
const accounts = computed(() => mailStore.accounts);
const attachmentLimits: Record<string, number> = {
  gmail: 18,
  qq: 35,
  netease: 35,
  icloud: 15,
  outlook: 15,
  sina: 15,
  custom: 20,
};
const selectedAccount = computed(() => accounts.value.find((account: any) => account.id === fromAccountId.value));
const attachmentLimitMb = computed(() => attachmentLimits[selectedAccount.value?.provider || ''] || 15);
const totalAttachmentBytes = computed(() => attachments.value.reduce((total, item) => total + Number(item.size || 0), 0));
const attachmentOverLimit = computed(() => totalAttachmentBytes.value > attachmentLimitMb.value * 1024 * 1024);

// ==================== 签名快速选择 ====================
const showSignaturePanel = ref(false);
const editorRef = ref<InstanceType<typeof import('../components/TiptapEditor.vue').default> | null>(null);
const activeSignatureId = ref<number | null>(null);
const composeKind = ref<ComposeKind>('new');
const userSigs = computed(() => signatureStore.signatures);
const availableUserSigs = computed(() => userSigs.value.filter(
  (signature) => !signature.account_id || signature.account_id === fromAccountId.value,
));
const activeSignatureName = computed(() => (
  activeSignatureId.value === null
    ? '无签名'
    : userSigs.value.find((signature) => signature.id === activeSignatureId.value)?.name || '已插入的签名'
));

function signatureDefaultLabel(signature: SignatureTemplate) {
  if (signature.is_default && signature.is_reply_default) return '双默认';
  if (signature.is_default) return '新邮件默认';
  if (signature.is_reply_default) return '回复默认';
  return '';
}

function isSignatureBodyEmpty(html: string) {
  return String(html || '')
    .replace(/<br\s*\/?>/gi, '')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;|\s/gi, '')
    .length === 0;
}

/** 使用可替换的签名块，切换签名时不会重复追加。 */
function insertSigToEditor(contentHtml: string, signatureId = -1) {
  editorRef.value?.setManagedSignature(
    signatureId,
    contentHtml,
    composeKind.value === 'reply' || composeKind.value === 'forward' ? 'start' : 'end',
  );
  activeSignatureId.value = signatureId >= 0 ? signatureId : null;
  showSignaturePanel.value = false;
}

function selectSignature(sig: SignatureTemplate | null) {
  if (!sig || isSignatureBodyEmpty(sig.content_html)) {
    editorRef.value?.setManagedSignature(null);
    activeSignatureId.value = null;
    showSignaturePanel.value = false;
    if (sig) showToast('该签名没有正文', 'info');
    return;
  }
  insertSigToEditor(sig.content_html, sig.id);
}

function signatureAccountLabel(accountId: string) {
  if (!accountId) return '全部邮箱';
  return accounts.value.find((account: any) => account.id === accountId)?.email || '指定邮箱';
}

let applyingComposeDraft = false;

function buildComposeWorkspaceSnapshot(): ComposeWorkspaceSnapshot {
  commitRecipientInputs();
  return {
    account_id: fromAccountId.value,
    to: [...toList.value],
    cc: [...ccList.value],
    bcc: [...bccList.value],
    subject: subject.value,
    body_html: bodyHtml.value,
    attachments: attachments.value.map((item) => ({ ...item })),
    draft_message_id: draftMessageId.value,
    draft_folder: draftFolder.value,
    compose_kind: composeKind.value,
    show_cc: showCc.value,
    show_bcc: showBcc.value,
    active_signature_id: activeSignatureId.value,
  };
}

function openSignatureManager() {
  mailStore.saveComposeWorkspace(buildComposeWorkspaceSnapshot());
  signatureStore.setEntrySource('compose');
  showSignaturePanel.value = false;
  window.dispatchEvent(new CustomEvent('flymail-navigate', { detail: 'signatures' }));
}

async function applyDefaultSignature() {
  if (composeKind.value === 'draft') return;
  const defaultSig = resolveDefaultSignature(userSigs.value, fromAccountId.value, composeKind.value);
  await nextTick();
  if (!editorRef.value) return;
  if (!defaultSig) {
    editorRef.value.setManagedSignature(null);
    activeSignatureId.value = null;
    return;
  }
  editorRef.value.setManagedSignature(
    defaultSig.id,
    defaultSig.content_html,
    composeKind.value === 'reply' || composeKind.value === 'forward' ? 'start' : 'end',
  );
  activeSignatureId.value = defaultSig.id;
}

async function applyComposeDraft(
  draft: Partial<ComposeWorkspaceSnapshot> | null = null,
  options: { applyDefaultSignature?: boolean } = {},
) {
  applyingComposeDraft = true;
  try {
    clearComposeForm();
    composeKind.value = draft?.compose_kind || (draft?.draft_message_id ? 'draft' : 'new');
    toList.value = [...(draft?.to || [])];
    ccList.value = [...(draft?.cc || [])];
    bccList.value = [...(draft?.bcc || [])];
    subject.value = draft?.subject || '';
    bodyHtml.value = draft?.body_html || '<p></p>';
    draftMessageId.value = draft?.draft_message_id || '';
    draftFolder.value = draft?.draft_folder || '';
    fromAccountId.value = draft?.account_id || mailStore.currentAccountId || accounts.value[0]?.id || '';
    attachments.value = (draft?.attachments || []).map((item) => ({ ...item }));
    showCc.value = Boolean(draft?.show_cc ?? ccList.value.length > 0);
    showBcc.value = Boolean(draft?.show_bcc ?? bccList.value.length > 0);
    activeSignatureId.value = draft?.active_signature_id ?? null;
    if (options.applyDefaultSignature !== false && composeKind.value !== 'draft') {
      await applyDefaultSignature();
    }
    markSavedSnapshot();
  } finally {
    applyingComposeDraft = false;
  }
}

// 初始化：先加载签名规则，再按新邮件/回复/转发/草稿场景建立编辑器。
onMounted(async () => {
  await signatureStore.ensureLoaded();
  if (mailStore.composeWorkspace) {
    await applyComposeDraft(mailStore.composeWorkspace, { applyDefaultSignature: false });
  } else {
    await applyComposeDraft(mailStore.consumeComposeDraft());
  }
});

watch(
  () => mailStore.composeDraft,
  async (draft) => {
    if (draft) {
      mailStore.clearComposeWorkspace();
      await applyComposeDraft(mailStore.consumeComposeDraft());
    }
  },
);

watch(fromAccountId, async (nextAccountId, previousAccountId) => {
  if (
    applyingComposeDraft
    || !previousAccountId
    || nextAccountId === previousAccountId
    || composeKind.value === 'draft'
  ) return;
  await applyDefaultSignature();
});

type RecipientField = 'to' | 'cc' | 'bcc';

function recipientRefs(field: RecipientField) {
  return {
    input: field === 'to' ? toInput : field === 'cc' ? ccInput : bccInput,
    list: field === 'to' ? toList : field === 'cc' ? ccList : bccList,
    autocomplete: field === 'to' ? toField : field === 'cc' ? ccField : bccField,
  };
}

function recipientEmail(value: string) {
  return value.match(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/)?.[0]?.toLowerCase() || '';
}

// 添加收件人
function addRecipient(field: RecipientField) {
  const { input, list, autocomplete } = recipientRefs(field);
  const address = input.value.trim().replace(/,$/, '');
  const email = recipientEmail(address);
  if (!email) return;
  if (!list.value.some((item) => recipientEmail(item) === email)) {
    list.value.push(address);
  }
  input.value = '';
  autocomplete.closeSuggestions();
}

function chooseSuggestion(field: RecipientField, item: ContactSuggestion) {
  const { input, list, autocomplete } = recipientRefs(field);
  const address = autocomplete.selectSuggestion(item);
  if (!list.value.some((existing) => recipientEmail(existing) === item.email.toLowerCase())) {
    list.value.push(address);
  }
  input.value = '';
}

function handleRecipientKeydown(field: RecipientField, event: KeyboardEvent) {
  const { autocomplete } = recipientRefs(field);
  const result = autocomplete.handleKeydown(event);
  if (result.selected) {
    chooseSuggestion(field, result.selected);
    return;
  }
  if (!result.handled && event.key === 'Enter') {
    event.preventDefault();
    addRecipient(field);
  }
}

function closeRecipientSuggestions(field: RecipientField) {
  window.setTimeout(() => {
    const { autocomplete } = recipientRefs(field);
    autocomplete.closeSuggestions();
    addRecipient(field);
  }, 120);
}

function commitRecipientInputs() {
  addRecipient('to');
  addRecipient('cc');
  addRecipient('bcc');
}

function getErrorMessage(e: any) {
  return e?.error || e?.message || e?.response?.data?.error || '网络错误';
}

function clearComposeForm() {
  toList.value = [];
  ccList.value = [];
  bccList.value = [];
  toInput.value = '';
  ccInput.value = '';
  bccInput.value = '';
  subject.value = '';
  bodyHtml.value = '';
  draftMessageId.value = '';
  draftFolder.value = '';
  attachments.value = [];
  showCc.value = false;
  showBcc.value = false;
  activeSignatureId.value = null;
  markSavedSnapshot();
}

function currentSnapshot() {
  return JSON.stringify({
    account_id: fromAccountId.value,
    compose_kind: composeKind.value,
    to: toList.value,
    cc: ccList.value,
    bcc: bccList.value,
    subject: subject.value,
    body_html: bodyHtml.value,
    attachments: attachments.value.map(a => a.path),
  });
}

function markSavedSnapshot() {
  savedSnapshot.value = currentSnapshot();
}

function hasUnsavedChanges() {
  return currentSnapshot() !== savedSnapshot.value;
}

function composePayload(action: 'send' | 'draft' | 'schedule') {
  return {
    account_id: fromAccountId.value,
    to: toList.value,
    cc: ccList.value,
    bcc: bccList.value,
    subject: subject.value,
    body_html: bodyHtml.value,
    draft_message_id: draftMessageId.value || undefined,
    draft_folder: draftFolder.value || undefined,
    action,
  };
}

// 发送邮件
async function sendMail() {
  commitRecipientInputs();
  if (attachmentOverLimit.value) {
    showToast(`附件总大小超过当前邮箱 ${attachmentLimitMb.value}MB 限制`, 'error');
    return;
  }
  if (toList.value.length === 0) {
    showToast('请输入收件人', 'info');
    return;
  }
  sending.value = true;
  try {
    await api.post('/messages/compose', {
      ...composePayload('send'),
      attachments: attachments.value.map(a => a.path),
    }) as any;
    showToast('发送成功', 'success');
    mailStore.clearComposeWorkspace();
    clearComposeForm();
    emit('sent', { account_id: fromAccountId.value });
  } catch (e: any) {
    showToast('发送失败: ' + getErrorMessage(e), 'error');
  } finally {
    sending.value = false;
  }
}

// 保存草稿
async function saveDraft() {
  savingDraft.value = true;
  try {
    const data = await api.post('/messages/compose', composePayload('draft')) as any;
    draftMessageId.value = data.draft_message_id || draftMessageId.value;
    draftFolder.value = data.draft_folder || draftFolder.value;
    markSavedSnapshot();
    showToast('草稿已保存', 'success');
  } catch (e: any) {
    showToast('保存草稿失败: ' + getErrorMessage(e), 'error');
  } finally {
    savingDraft.value = false;
  }
}

// 定时发送
async function scheduleMail() {
  if (attachmentOverLimit.value) {
    showToast(`附件总大小超过当前邮箱 ${attachmentLimitMb.value}MB 限制`, 'error');
    return;
  }
  if (toList.value.length === 0) {
    showToast('请输入收件人', 'info');
    return;
  }
  if (!isScheduleValid.value) {
    showToast('请选择未来的时间', 'info');
    return;
  }
  // 组装 ISO 时间字符串
  const y = scheduleYear.value;
  const m = String(scheduleMonth.value).padStart(2, '0');
  const d = String(scheduleDay.value).padStart(2, '0');
  const h = String(scheduleHour.value).padStart(2, '0');
  const min = String(scheduleMinute.value).padStart(2, '0');
  const scheduleTimeISO = `${y}-${m}-${d}T${h}:${min}:00`;
  try {
    await api.post('/messages/compose', {
      ...composePayload('schedule'),
      attachments: attachments.value.map(a => a.path),
      schedule_time: scheduleTimeISO,
    });
    showScheduleModal.value = false;
    // 不跳转页面，显示成功弹窗
    scheduleSuccessTime.value = schedulePreview.value;
    showScheduleSuccessModal.value = true;
  } catch (e: any) {
    showToast('设置定时发送失败: ' + getErrorMessage(e), 'error');
  }
}

// 关闭邮件
function discardMail() {
  if (hasUnsavedChanges()) {
    showConfirm('确定关闭写邮件？未保存的内容将丢失', () => {
      mailStore.clearComposeWorkspace();
      emit('discard');
    });
  } else {
    mailStore.clearComposeWorkspace();
    emit('discard');
  }
}

// 附件处理
async function handleFileSelect(event: Event) {
  const input = event.target as HTMLInputElement;
  if (!input.files) return;
  for (const file of Array.from(input.files)) {
    await uploadFile(file);
  }
  input.value = '';
}

async function handleDrop(event: DragEvent) {
  isDragging.value = false;
  if (!event.dataTransfer?.files) return;
  for (const file of Array.from(event.dataTransfer.files)) {
    await uploadFile(file);
  }
}

async function addNasAttachment(path: string) {
  showNasPicker.value = false;
  try {
    const data = await api.post('/messages/register-nas-attachment', { path }) as any;
    if (!attachments.value.some((item) => item.path === data.path)) {
      attachments.value.push({
        filename: data.filename,
        size: data.size,
        path: data.path,
        source: 'nas',
      });
    }
  } catch (e: any) {
    showToast('添加 NAS 附件失败: ' + getErrorMessage(e), 'error');
  }
}

async function uploadFile(file: File) {
  const formData = new FormData();
  formData.append('file', file);
  try {
    const data = await api.post('/messages/upload-attachment', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }) as any;
    attachments.value.push({
      filename: data.filename,
      size: data.size,
      path: data.path,
      source: 'local',
    });
  } catch (e: any) {
    showToast('上传附件失败: ' + file.name, 'error');
  }
}

async function removeAttachment(index: number) {
  const att = attachments.value[index];
  if (att.source !== 'nas') {
    try {
      await api.delete('/messages/upload-attachment', { params: { path: att.path } });
    } catch {
      // 删除失败也从前端移除
    }
  }
  attachments.value.splice(index, 1);
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}
</script>

<style scoped>
.compose-page {
  width: 100%;
  min-height: 0;
  min-width: 0;
  position: relative;
  background: var(--bg-primary);
}

/* 拖拽上传遮罩 */
.drop-overlay {
  position: absolute;
  inset: 0;
  z-index: 100;
  background: var(--ui-accent-soft);
  border: 2px dashed var(--ui-accent);
  border-radius: var(--border-radius-lg, 8px);
  display: flex;
  align-items: center;
  justify-content: center;
}

.drop-hint {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  color: var(--ui-accent);
  font-size: var(--text-base);
  font-weight: 500;
}

/* 工具栏 */
.compose-toolbar {
  position: relative;
  z-index: 20;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--border-color);
  background: var(--bg-secondary);
  min-width: 0;
  overflow: visible;
}

.toolbar-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 12px;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  background: var(--bg-primary);
  color: var(--text-primary);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}

.toolbar-btn:hover {
  background: var(--bg-hover);
}

.toolbar-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.toolbar-btn.primary {
  background: var(--ui-accent);
  color: var(--ui-text-inverse);
  border-color: var(--ui-accent);
}

.toolbar-btn.primary:hover {
  opacity: 0.9;
}

.toolbar-btn.danger:hover {
  background: var(--ui-danger);
  color: var(--ui-text-inverse);
  border-color: var(--ui-danger);
}

.toolbar-spacer {
  flex: 1;
}

/* 表单 */
.compose-form {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
  min-width: 0;
  width: 100%;
  padding: 0 16px 16px;
  display: flex;
  flex-direction: column;
}

.form-row {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  align-items: center;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid var(--border-color);
  min-width: 0;
}

.compose-field-label {
  width: 100%;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  text-align: right;
}

.compose-field-control {
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
}

.compose-from-control {
  display: flex;
  align-items: center;
  gap: 10px;
}

.compose-field-control > .form-input {
  width: 100%;
  box-sizing: border-box;
}

.form-input {
  flex: 1;
  min-width: 0;
  padding: 6px 10px;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  background: var(--bg-primary);
  color: var(--text-primary);
  font-size: 14px;
  outline: none;
}

.form-input:focus {
  border-color: var(--ui-accent);
}

.form-select {
  flex: 1;
  min-width: 0;
  padding: 6px 10px;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  background: var(--bg-primary);
  color: var(--text-primary);
  font-size: 14px;
  outline: none;
}

/* 标签输入 */
.tag-input {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 4px 8px;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  background: var(--bg-primary);
  min-height: 36px;
  align-items: center;
  position: relative;
}

.tag-input:focus-within {
  border-color: var(--ui-accent);
}

.tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  background: var(--ui-accent);
  color: var(--ui-text-inverse);
  border-radius: 4px;
  font-size: 12px;
}

.tag-remove {
  background: none;
  border: none;
  color: var(--ui-text-inverse-muted);
  cursor: pointer;
  font-size: 14px;
  padding: 0 2px;
  line-height: 1;
}

.tag-remove:hover {
  color: var(--ui-text-inverse);
}

.contact-suggestions {
  position: absolute;
  left: 0;
  right: 0;
  top: calc(100% + 4px);
  z-index: 30;
  display: flex;
  flex-direction: column;
  max-height: 240px;
  overflow-y: auto;
  padding: 4px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--bg-primary);
  box-shadow: var(--ui-shadow-md);
}

.contact-suggestions button {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text-primary);
  text-align: left;
  cursor: pointer;
}

.contact-suggestions button:hover,
.contact-suggestions button.active {
  background: var(--bg-hover);
}

.contact-suggestions strong,
.contact-suggestions small {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.contact-suggestions strong {
  flex: 1 1 auto;
}

.contact-suggestions small {
  flex: 0 1 46%;
  color: var(--text-tertiary);
}

.tag-input-field {
  flex: 1;
  min-width: 120px;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text-primary);
  font-size: 14px;
  padding: 2px 0;
}

.text-btn {
  min-width: var(--ui-control-md);
  flex: 0 0 auto;
  padding: 0 6px;
  border: none;
  background: none;
  color: var(--ui-accent);
  cursor: pointer;
  font-size: 12px;
}

.text-btn:hover {
  text-decoration: underline;
}

/* 编辑器行：独立于 form-row 布局，占满剩余空间 */
.editor-row {
  padding: 8px 0;
  flex: 1;
  min-height: 200px;
  overflow: hidden;
  min-width: 0;
}

/* 抄送/密送链接 */
.cc-links {
  display: flex;
  gap: 8px;
  margin-left: 8px;
  flex-shrink: 0;
}

/* 附件区域 */
.attachments-section {
  padding: 4px 0;
  flex-shrink: 0;
  min-width: 0;
}

.attachments-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.attachment-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.attachments-count {
  font-size: 12px;
  color: var(--text-tertiary);
}

.attachments-count.error {
  color: var(--ui-danger);
  font-weight: 600;
}

.upload-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border: 1px dashed var(--border-color);
  border-radius: 6px;
  color: var(--ui-accent);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}

.upload-btn:hover {
  background: var(--ui-fill-hover);
  border-color: var(--ui-accent);
}

.hidden-input {
  display: none;
}

.attachments-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.attachment-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  background: var(--bg-secondary);
  border-radius: 6px;
  font-size: 12px;
  color: var(--text-primary);
}

.att-name {
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.att-source {
  padding: 1px 5px;
  border-radius: 999px;
  background: var(--ui-accent);
  color: var(--ui-text-inverse);
  font-size: 10px;
}

.att-size {
  color: var(--text-tertiary);
}

.att-remove {
  background: none;
  border: none;
  color: var(--text-tertiary);
  cursor: pointer;
  font-size: 16px;
  padding: 0 2px;
  line-height: 1;
}

.att-remove:hover {
  color: var(--ui-danger);
}

/* 弹窗 */
.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 200;
  background: var(--ui-scrim);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: visible;
}

.modal-content {
  background: var(--bg-primary);
  border-radius: var(--border-radius-lg, 8px);
  padding: 24px;
  width: 360px;
  max-width: 90vw;
  box-shadow: var(--ui-shadow-md);
  overflow: visible;
}

.modal-content h3 {
  margin: 0 0 8px;
  font-size: 16px;
  color: var(--text-primary);
}

.modal-desc {
  margin: 0 0 16px;
  font-size: 13px;
  color: var(--text-secondary);
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}

/* 定时发送弹窗 */
.schedule-modal {
  width: 420px;
  max-height: 80vh;
}

.schedule-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.schedule-header h3 {
  margin: 0;
}

/* 日期/时间选择卡片 */
.schedule-card {
  background: var(--bg-secondary);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 8px;
}

.schedule-card-row {
  display: flex;
  align-items: center;
  gap: 2px;
  justify-content: center;
}

.sc-gap {
  width: 8px;
  flex-shrink: 0;
}

.sc-select {
  padding: 5px 18px 5px 6px;
  border: 1px solid var(--border-color);
  border-radius: 5px;
  background: var(--bg-primary);
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 500;
  outline: none;
  cursor: pointer;
  appearance: auto;
  -webkit-appearance: menulist;
  transition: border-color 0.15s;
  width: 44px;
  text-align: center;
  flex-shrink: 0;
}

.sc-select-wide {
  width: 60px;
}

.sc-select:focus {
  border-color: var(--ui-accent);
}

.sc-sep {
  font-size: 16px;
  color: var(--text-secondary);
  font-weight: 300;
  line-height: 1;
  user-select: none;
}

/* 预览条 */
.schedule-preview-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px 12px;
  background: var(--ui-accent-soft);
  border: 1px solid color-mix(in srgb, var(--ui-accent) 24%, var(--ui-border));
  border-radius: 6px;
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 4px;
}

.schedule-preview-bar strong {
  color: var(--ui-accent);
  font-weight: 600;
}

/* 定时发送成功弹窗 */
.success-modal {
  width: 320px;
  text-align: center;
}

.success-icon {
  margin-bottom: 12px;
}

.success-desc {
  margin: 0;
  font-size: 14px;
  color: var(--text-secondary);
  line-height: 1.5;
}

.success-desc strong {
  color: var(--text-primary);
}

/* 待发邮件列表 */
.scheduled-list {
  margin-top: 12px;
  border-top: 1px solid var(--border-color);
  padding-top: 10px;
}

.scheduled-list-title {
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 8px;
  font-weight: 500;
}

.scheduled-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 8px;
  border-radius: 6px;
  margin-bottom: 4px;
  background: var(--bg-secondary);
  transition: background 0.15s;
}

.scheduled-item:hover {
  background: var(--bg-hover);
}

.scheduled-item-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.scheduled-item-subject {
  font-size: 13px;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.scheduled-item-time {
  font-size: 11px;
  color: var(--text-secondary);
}

.scheduled-item-cancel {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  background: none;
  color: var(--text-secondary);
  cursor: pointer;
  border-radius: 4px;
  flex-shrink: 0;
  margin-left: 8px;
  transition: all 0.15s;
}

.scheduled-item-cancel:hover {
  background: var(--ui-danger-soft);
  color: var(--ui-danger);
}

/* 确认对话框 */
.confirm-modal {
  width: 320px;
  text-align: center;
}

.confirm-icon {
  margin-bottom: 12px;
}

.confirm-text {
  margin: 0 0 4px;
  font-size: 14px;
  color: var(--text-primary);
  line-height: 1.5;
}

/* Toast 通知 */
.toast {
  position: fixed;
  top: 20px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 300;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 20px;
  border-radius: 8px;
  font-size: 14px;
  box-shadow: var(--ui-shadow-sm);
  pointer-events: none;
}

.toast.success {
  background: var(--ui-success);
  color: var(--ui-text-inverse);
}

.toast.error {
  background: var(--ui-danger);
  color: var(--ui-text-inverse);
}

.toast.info {
  background: var(--bg-primary);
  color: var(--text-primary);
  border: 1px solid var(--border-color);
}

/* Toast 动画 */
.toast-enter-active {
  transition: all 0.3s ease-out;
}

.toast-leave-active {
  transition: all 0.2s ease-in;
}

.toast-enter-from {
  opacity: 0;
  transform: translateX(-50%) translateY(-20px);
}

.toast-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(-10px);
}

/* 移动端适配 */
@media (max-width: 768px) {
  .compose-toolbar {
    padding: 8px 12px;
    gap: 4px;
    align-items: stretch;
  }

  .toolbar-btn span {
    display: none;
  }

  .toolbar-btn {
    padding: 8px;
  }

  .toolbar-spacer {
    display: none;
  }

  .compose-form {
    padding: 0 12px 12px;
  }

  .form-row {
    flex-wrap: wrap;
    align-items: flex-start;
    gap: 8px;
    padding: 10px 0;
  }

  .form-row label {
    width: 100%;
    font-size: 12px;
    text-align: left;
  }

  .form-input,
  .form-select,
  .tag-input {
    width: 100%;
  }

  .cc-links {
    width: 100%;
    margin-left: 0;
  }

  .tag-input-field {
    min-width: 80px;
  }

  .attachments-list {
    width: 100%;
    flex-direction: column;
  }

  .attachment-item {
    width: 100%;
    min-width: 0;
  }

  .att-name {
    flex: 1;
    max-width: none;
    min-width: 0;
  }

  .modal-content {
    width: 90vw;
    padding: 16px;
  }

  .schedule-modal {
    width: 90vw;
  }

  .sc-select {
    font-size: 13px;
    padding: 4px 16px 4px 6px;
  }
}

/* ==================== 签名快速选择 ==================== */
.sig-dropdown {
  position: relative;
  display: inline-flex;
}

.sig-panel {
  position: absolute;
  top: 100%;
  right: 0;
  z-index: 200;
  width: min(310px, calc(100vw - 24px));
  max-height: min(440px, calc(100vh - 120px));
  margin-top: 6px;
  overflow-y: auto;
  padding: 8px;
  border: 1px solid var(--ui-border-strong);
  border-radius: var(--ui-radius-lg);
  background: var(--ui-surface-floating);
  box-shadow: var(--ui-shadow-md);
}

.sig-current-summary {
  display: grid;
  gap: 3px;
  padding: 8px 10px 10px;
  border-bottom: 1px solid var(--ui-border);
}

.sig-current-summary span {
  color: var(--ui-text-3);
  font-size: 11px;
}

.sig-current-summary strong {
  overflow: hidden;
  color: var(--ui-text-1);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sig-quick-item,
.sig-manage-button {
  width: 100%;
  min-height: 44px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 10px;
  border: 0;
  border-radius: var(--ui-radius-md);
  background: transparent;
  color: var(--ui-text-1);
  text-align: left;
  cursor: pointer;
}

.sig-quick-item:hover,
.sig-manage-button:hover {
  background: var(--ui-fill-hover);
}

.sig-quick-item.active {
  background: var(--ui-fill-selected);
  color: var(--ui-accent);
}

.sig-quick-copy {
  min-width: 0;
  display: grid;
  gap: 3px;
}

.sig-quick-copy strong,
.sig-quick-copy small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sig-quick-copy strong {
  font-size: 13px;
}

.sig-quick-copy small {
  color: var(--ui-text-3);
  font-size: 11px;
}

.sig-quick-badges {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 6px;
}

.sig-quick-badge {
  padding: 2px 6px;
  border-radius: 999px;
  background: var(--ui-accent-soft);
  color: var(--ui-accent);
  font-size: 10px;
}

.sig-quick-check {
  color: var(--ui-accent);
  font-weight: 700;
}

.sig-panel-divider {
  height: 1px;
  margin: 6px 2px;
  background: var(--ui-border);
}

.sig-manage-button {
  color: var(--ui-accent);
  font-weight: 650;
}

.sig-empty-hint {
  padding: 14px 10px;
  color: var(--ui-text-3);
  font-size: 12px;
  text-align: center;
}

@media (max-width: 768px) {
  .sig-panel {
    position: fixed;
    inset: auto 0 0;
    width: 100%;
    max-height: 62vh;
    margin: 0;
    padding: 12px 14px max(18px, env(safe-area-inset-bottom));
    border-radius: 14px 14px 0 0;
    z-index: 1000;
  }
}
</style>

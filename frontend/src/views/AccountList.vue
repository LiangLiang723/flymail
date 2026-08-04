<template>
  <PageFrame template="management" width="fluid" class="account-page ui-page">
    <template #header>
      <PageHeader title="账号管理" description="添加、分组和维护用于收发邮件的邮箱账号。" />
    </template>
    <template #toolbar>
      <PageToolbar>
        <template #start>
          <UiSegmentedControl
            :model-value="sortBy"
            :options="sortOptions"
            label="账号分组方式"
            @update:model-value="setSortBy"
          />
          <UiBadge size="md">{{ mailStore.accounts.length }} 个账号</UiBadge>
        </template>
        <template #end>
          <UiButton variant="primary" @click="showAddDialog = true">
            <template #leading>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
                <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
              </svg>
            </template>
            添加账号
          </UiButton>
        </template>
      </PageToolbar>
    </template>

    <div class="management-stack account-stack">
    <!-- 加载状态 -->
    <UiLoadingState v-if="loading" panel label="正在加载邮箱账号…" />

    <!-- 空状态 -->
    <UiEmptyState
      v-else-if="mailStore.accounts.length === 0 && deleteJobs.length === 0"
      panel
      title="还没有添加邮箱账号"
      description="点击上方「添加账号」按钮，添加你的邮箱即可开始使用"
    >
      <template #icon>
        <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4">
          <rect x="2" y="4" width="20" height="16" rx="2"/>
          <path d="M22 4L12 13L2 4"/>
        </svg>
      </template>
      <UiButton variant="primary" @click="showAddDialog = true">添加账号</UiButton>
    </UiEmptyState>

    <!-- 账号列表 -->
    <div v-else class="account-sections">
      <div v-if="deleteJobs.length" class="delete-jobs">
        <div v-for="job in deleteJobs" :key="job.id" class="delete-job">
          <div class="delete-job-main">
            <span class="delete-job-title">正在删除 {{ job.current_folder || '邮箱账号' }}</span>
            <span class="delete-job-meta">{{ deleteJobText(job) }}</span>
          </div>
          <div class="delete-progress">
            <span class="delete-progress-bar" :style="{ width: deleteJobPercent(job) + '%' }"></span>
          </div>
        </div>
      </div>
      <div v-for="section in groupedAccounts" :key="section.key" class="account-section">
        <!-- 分组标题 -->
        <div class="section-header">
          <span class="section-icon" v-html="section.icon"></span>
          <h3 class="section-title">{{ section.title }}</h3>
          <UiBadge>{{ section.accounts.length }}</UiBadge>
        </div>
        <!-- 账号卡片 -->
        <div class="account-list account-card-grid">
          <div v-for="account in section.accounts" :key="account.id" class="account-card" @click="openEditDialog(account)">
            <AccountIcon :account="account" :size="36" decorative />
            <!-- 账号信息 -->
            <div class="account-info">
              <div class="info-main">
                <span class="account-name">
                  <span v-if="account.remark" class="name-remark">{{ account.remark }}</span>
                  <span v-if="!account.remark" class="name-email">{{ account.email }}</span>
                </span>
                <span v-if="account.remark && !account.hide_email" class="account-email-sub">{{ account.email }}</span>
              </div>
              <div class="info-meta">
                <span class="meta-provider">{{ providerName(account.provider) }}</span>
                <span class="meta-sep">·</span>
                <UiBadge :tone="statusTone(account.status)">{{ statusText(account.status) }}</UiBadge>
              </div>
            </div>
            <!-- 操作按钮 -->
            <div class="card-actions">
              <button v-if="mailStore.reauthAccountIds.has(account.id) || account.status === 'offline'" class="btn-reauth-card" @click.stop="reconnectAccount(account)" :title="account.status === 'offline' ? '重新连接' : '重新授权'">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 4v6h-6"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
                {{ account.status === 'offline' ? '重新连接' : '重新授权' }}
              </button>
              <button class="edit-btn" @click.stop="openEditDialog(account)" title="编辑">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                  <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                </svg>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 添加账号对话框 -->
    <div v-if="showAddDialog" class="dialog-overlay" @click.self="showAddDialog = false">
      <div class="dialog">
        <h3 class="dialog-title">添加邮箱账号</h3>
        <p class="dialog-desc">选择邮箱服务商，授权后即可使用</p>
        <div class="provider-grid">
          <button v-for="p in providers" :key="p.type" class="provider-card" :class="{ active: selectedProvider === p.type }" @click="selectedProvider = p.type">
            <div class="provider-icon" v-html="p.icon"></div>
            <span class="provider-name">{{ p.name }}</span>
          </button>
        </div>
        <div class="form-field toggle-field">
          <span class="toggle-label">获取历史邮件</span>
          <button class="toggle-switch" :class="{ active: fetchHistory }" type="button" aria-label="获取历史邮件" :aria-pressed="fetchHistory" @click="fetchHistory = !fetchHistory">
            <span class="toggle-knob"></span>
          </button>
        </div>
        <div class="dialog-actions">
          <button class="btn btn-secondary" @click="showAddDialog = false">取消</button>
          <button class="btn btn-primary" @click="startAuth" :disabled="!selectedProvider">
            {{ ['qq', 'netease', 'icloud', 'sina', 'custom'].includes(selectedProvider) ? '下一步' : '授权登录' }}
          </button>
        </div>
      </div>
    </div>

    <!-- QQ邮箱授权码对话框 -->
    <div v-if="showQQDialog" class="dialog-overlay" @click.self="showQQDialog = false">
      <div class="dialog">
        <h3 class="dialog-title">添加QQ邮箱</h3>
        <p class="dialog-desc">请输入QQ邮箱地址和授权码</p>
        <div class="qq-form">
          <div class="form-field">
            <label class="field-label">QQ邮箱地址</label>
            <input v-model="qqForm.email" class="input" type="email" placeholder="example@qq.com" />
          </div>
          <div class="form-field">
            <label class="field-label">授权码</label>
            <input v-model="qqForm.auth_code" class="input" type="password" placeholder="QQ邮箱授权码" />
            <p class="field-hint">
              授权码需要在QQ邮箱设置中开启IMAP/SMTP服务后获取
              <a href="https://service.mail.qq.com/detail?search=SMTP/IMAP" target="_blank" class="hint-link">查看教程</a>
            </p>
          </div>
        </div>
        <div class="dialog-actions">
          <button class="btn btn-secondary" @click="showQQDialog = false">取消</button>
          <button class="btn btn-primary" @click="addQQAccount" :disabled="!qqForm.email || !qqForm.auth_code">添加账号</button>
        </div>
      </div>
    </div>

    <!-- 网易邮箱授权码对话框 -->
    <div v-if="showNeteaseDialog" class="dialog-overlay" @click.self="showNeteaseDialog = false">
      <div class="dialog">
        <h3 class="dialog-title">添加网易邮箱</h3>
        <p class="dialog-desc">请输入网易邮箱地址（163/126/188/yeah.net）和授权码</p>
        <div class="qq-form">
          <div class="form-field">
            <label class="field-label">邮箱地址</label>
            <input v-model="neteaseForm.email" class="input" type="email" placeholder="example@163.com / @126.com / @188.com" />
          </div>
          <div class="form-field">
            <label class="field-label">授权码</label>
            <input v-model="neteaseForm.auth_code" class="input" type="password" placeholder="网易邮箱授权码" />
            <p class="field-hint">
              授权码需要在网易邮箱设置中开启IMAP/SMTP服务后获取
              <a href="https://help.mail.163.com/searchFAQ.do?m=search&word=POP3/SMTP/IMAP" target="_blank" class="hint-link">查看教程</a>
            </p>
          </div>
        </div>
        <div class="dialog-actions">
          <button class="btn btn-secondary" @click="showNeteaseDialog = false">取消</button>
          <button class="btn btn-primary" @click="addNeteaseAccount" :disabled="!neteaseForm.email || !neteaseForm.auth_code">添加账号</button>
        </div>
      </div>
    </div>

    <!-- iCloud邮箱应用专用密码对话框 -->
    <div v-if="showICloudDialog" class="dialog-overlay" @click.self="showICloudDialog = false">
      <div class="dialog">
        <h3 class="dialog-title">添加iCloud邮箱</h3>
        <p class="dialog-desc">请输入iCloud邮箱地址和应用专用密码</p>
        <div class="qq-form">
          <div class="form-field">
            <label class="field-label">iCloud邮箱地址</label>
            <input v-model="icloudForm.email" class="input" type="email" placeholder="example@icloud.com" />
          </div>
          <div class="form-field">
            <label class="field-label">应用专用密码</label>
            <input v-model="icloudForm.auth_code" class="input" type="password" placeholder="Apple ID 应用专用密码" />
            <p class="field-hint">
              应用专用密码需要在 Apple ID 账户页面生成
              <a href="https://appleid.apple.com/account/manage" target="_blank" class="hint-link">前往生成</a>
            </p>
          </div>
        </div>
        <div class="dialog-actions">
          <button class="btn btn-secondary" @click="showICloudDialog = false">取消</button>
          <button class="btn btn-primary" @click="addICloudAccount" :disabled="!icloudForm.email || !icloudForm.auth_code">添加账号</button>
        </div>
      </div>
    </div>

    <!-- 新浪邮箱授权码对话框 -->
    <div v-if="showSinaDialog" class="dialog-overlay" @click.self="showSinaDialog = false">
      <div class="dialog">
        <h3 class="dialog-title">添加新浪邮箱</h3>
        <p class="dialog-desc">请输入新浪邮箱地址和客户端授权码</p>
        <div class="qq-form">
          <div class="form-field">
            <label class="field-label">邮箱地址</label>
            <input v-model="sinaForm.email" class="input" type="email" placeholder="example@sina.com" />
          </div>
          <div class="form-field">
            <label class="field-label">客户端授权码</label>
            <input v-model="sinaForm.auth_code" class="input" type="password" autocomplete="new-password" placeholder="新浪邮箱客户端授权码" />
            <p class="field-hint">请在新浪邮箱网页设置中开启 IMAP/SMTP，并生成客户端授权码。</p>
          </div>
        </div>
        <div class="dialog-actions">
          <button class="btn btn-secondary" @click="showSinaDialog = false">取消</button>
          <button class="btn btn-primary" @click="addSinaAccount" :disabled="!sinaForm.email || !sinaForm.auth_code">添加账号</button>
        </div>
      </div>
    </div>

    <!-- 通用 IMAP/SMTP 对话框 -->
    <div v-if="showCustomDialog" class="dialog-overlay" @click.self="showCustomDialog = false">
      <div class="dialog dialog-wide">
        <h3 class="dialog-title">添加其他邮箱</h3>
        <p class="dialog-desc">填写邮箱服务商提供的加密 IMAP/SMTP 参数。禁止明文连接和内网服务器。</p>
        <div class="custom-form">
          <div class="form-field">
            <label class="field-label">邮箱地址</label>
            <input v-model="customForm.email" class="input" type="email" placeholder="user@example.com" @input="syncCustomUsername" />
          </div>
          <div class="form-field">
            <label class="field-label">登录用户名</label>
            <input v-model="customForm.username" class="input" type="text" placeholder="默认使用邮箱地址" />
          </div>
          <div class="form-field custom-span-2">
            <label class="field-label">密码或授权码</label>
            <input v-model="customForm.auth_code" class="input" type="password" autocomplete="new-password" placeholder="邮箱密码、授权码或应用专用密码" />
          </div>
          <div class="form-section custom-span-2">收件服务器</div>
          <div class="form-field">
            <label class="field-label">IMAP 主机</label>
            <input v-model="customForm.imap_host" class="input" type="text" placeholder="imap.example.com" />
          </div>
          <div class="server-row">
            <div class="form-field">
              <label class="field-label">端口</label>
              <input v-model.number="customForm.imap_port" class="input" type="number" min="1" max="65535" />
            </div>
            <div class="form-field">
              <label class="field-label">加密</label>
              <select v-model="customForm.imap_ssl" class="input" @change="updateCustomPort('imap')">
                <option value="ssl">SSL/TLS</option>
                <option value="starttls">STARTTLS</option>
              </select>
            </div>
          </div>
          <div class="form-section custom-span-2">发件服务器</div>
          <div class="form-field">
            <label class="field-label">SMTP 主机</label>
            <input v-model="customForm.smtp_host" class="input" type="text" placeholder="smtp.example.com" />
          </div>
          <div class="server-row">
            <div class="form-field">
              <label class="field-label">端口</label>
              <input v-model.number="customForm.smtp_port" class="input" type="number" min="1" max="65535" />
            </div>
            <div class="form-field">
              <label class="field-label">加密</label>
              <select v-model="customForm.smtp_ssl" class="input" @change="updateCustomPort('smtp')">
                <option value="ssl">SSL/TLS</option>
                <option value="starttls">STARTTLS</option>
              </select>
            </div>
          </div>
        </div>
        <p class="field-hint">保存前会同时测试 IMAP 文件夹读取与 SMTP 登录；任一失败都不会保存账号。</p>
        <div class="dialog-actions">
          <button class="btn btn-secondary" @click="showCustomDialog = false">取消</button>
          <button class="btn btn-primary" @click="addCustomAccount" :disabled="!customForm.email || !customForm.auth_code || !customForm.imap_host || !customForm.smtp_host">测试并添加</button>
        </div>
      </div>
    </div>

    <!-- 编辑账号对话框 -->
    <div v-if="showEditDialog" class="dialog-overlay" @click.self="closeEditDialog">
      <div class="dialog">
        <h3 class="dialog-title">编辑账号</h3>
        <p class="dialog-desc">{{ editingAccount?.email }}</p>
        <div class="edit-form">
          <div v-if="editingAccount" class="form-field account-icon-editor">
            <label class="field-label">邮箱图标</label>
            <div class="account-icon-editor__current">
              <AccountIcon :account="editingAccount" :size="48" />
              <div class="account-icon-editor__actions">
                <UiButton variant="secondary" size="sm" :disabled="iconSaving" @click="showIconPresets = !showIconPresets">
                  选择内置图标
                </UiButton>
                <UiButton variant="secondary" size="sm" :disabled="iconSaving" @click="iconFileInput?.click()">
                  上传图片
                </UiButton>
                <UiButton
                  v-if="editingAccount.icon_type !== 'default'"
                  variant="ghost"
                  size="sm"
                  :disabled="iconSaving"
                  @click="resetAccountIcon"
                >
                  恢复默认图标
                </UiButton>
              </div>
              <input
                ref="iconFileInput"
                class="account-icon-file-input"
                type="file"
                accept="image/jpeg,image/png,image/webp"
                @change="selectIconFile"
              />
            </div>
            <div v-if="showIconPresets" class="account-icon-preset-grid" aria-label="选择内置邮箱图标">
              <button
                v-for="preset in ACCOUNT_ICON_PRESETS"
                :key="preset.id"
                class="account-icon-preset"
                :class="{ active: editingAccount.icon_type === 'preset' && editingAccount.icon_value === preset.id }"
                type="button"
                :disabled="iconSaving"
                @click="selectAccountIconPreset(preset.id)"
              >
                <span v-html="preset.svg"></span>
                <small>{{ preset.label }}</small>
              </button>
            </div>
            <span class="field-hint">支持 JPG、PNG、WebP，裁剪后统一保存为 256×256 图标。</span>
          </div>
          <div class="form-field">
            <label class="field-label">备注名</label>
            <input v-model="editForm.remark" class="input" type="text" placeholder="如：工作邮箱" />
          </div>
          <div class="form-field">
            <label class="field-label">分组</label>
            <input v-model="editForm.group_name" class="input" type="text" placeholder="如：工作、个人" />
            <div v-if="existingGroups.length" class="group-tags">
              <button v-for="g in existingGroups" :key="g" class="group-tag" :class="{ active: editForm.group_name === g }" @click="editForm.group_name = g">{{ g }}</button>
            </div>
          </div>
          <div class="form-field">
            <label class="field-label">新邮件轮询间隔（秒）</label>
            <input v-model.number="editForm.poll_interval_seconds" class="input" type="number" min="5" max="3600" step="1" />
            <span class="field-hint">所有在线账号都会按该间隔兜底拉新；支持 IDLE 的账号仍优先使用实时通知。</span>
          </div>
          <div class="form-field toggle-field">
            <span class="toggle-label">隐藏邮箱地址</span>
            <button class="toggle-switch" :class="{ active: editForm.hide_email }" type="button" aria-label="隐藏邮箱地址" :aria-pressed="editForm.hide_email" @click="editForm.hide_email = !editForm.hide_email">
              <span class="toggle-knob"></span>
            </button>
          </div>
        </div>
        <div class="dialog-actions">
          <button class="btn btn-secondary" @click="closeEditDialog">取消</button>
          <button v-if="editingAccount?.status === 'offline' || mailStore.reauthAccountIds.has(editingAccount?.id)" class="btn btn-secondary" @click="reconnectAccount(editingAccount!)">
            {{ editingAccount?.status === 'offline' ? '重新连接' : '重新授权' }}
          </button>
          <button v-if="editingAccount?.status !== 'offline'" class="btn btn-secondary" @click="confirmDisable(editingAccount!)">禁用账户</button>
          <button class="btn btn-danger-text" @click="confirmDelete(editingAccount!)">删除账户</button>
          <button class="btn btn-primary" @click="saveEdit">保存</button>
        </div>
      </div>
    </div>

    <AccountIconCropDialog
      v-if="cropSource"
      :src="cropSource"
      :natural-width="cropNaturalWidth"
      :natural-height="cropNaturalHeight"
      :busy="iconSaving"
      @close="closeCropDialog"
      @reselect="reselectIconFile"
      @confirm="uploadCroppedIcon"
      @error="ui.error($event)"
    />
    </div>
  </PageFrame>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue';
import AccountIcon from '../components/account/AccountIcon.vue';
import AccountIconCropDialog from '../components/account/AccountIconCropDialog.vue';
import PageFrame from '../components/layout/PageFrame.vue';
import PageHeader from '../components/layout/PageHeader.vue';
import PageToolbar from '../components/layout/PageToolbar.vue';
import UiBadge from '../components/ui/UiBadge.vue';
import UiButton from '../components/ui/UiButton.vue';
import UiEmptyState from '../components/ui/UiEmptyState.vue';
import UiLoadingState from '../components/ui/UiLoadingState.vue';
import UiSegmentedControl from '../components/ui/UiSegmentedControl.vue';
import api from '../utils/api';
import { useUIStore } from '../stores/ui';
import { useMailStore } from '../stores/mail';
import { ACCOUNT_ICON_PRESETS, type AccountIconPresetId } from '../utils/account-icon-presets';
import { providerIcon, providerName } from '../utils/provider';
import { authWindowBlockedMessage, closeAuthWindow, navigateAuthWindow, openAuthWindowSync } from '../utils/oauthWindow';
import { useWebSocket } from '../composables/useWebSocket';

const ui = useUIStore();
const mailStore = useMailStore();

// WebSocket 实时同步：监听账号连接状态变化，自动更新账号列表状态
function handleWsMessage(data: any) {
  if (data.type === 'connection_status') {
    const account = mailStore.accounts.find(a => a.id === data.account_id);
    if (account) {
      if (account.status === 'offline') return;
      if (data.status === 'reauth_needed') {
        account.status = 'reauth_needed';
        mailStore.reauthAccountIds.add(data.account_id);
      } else {
        account.status = data.status === 'connected' ? 'connected' : 'error';
        if (data.status === 'connected') {
          mailStore.reauthAccountIds.delete(data.account_id);
        }
      }
    }
  }
}
const { connect: connectWs } = useWebSocket(handleWsMessage);

// 使用 store 中的账号列表，不再维护本地副本
const loading = ref(true);
const sortBy = ref<'group' | 'platform'>('platform');
const sortOptions = [
  { value: 'group', label: '按分组' },
  { value: 'platform', label: '按平台' },
];

function setSortBy(value: string) {
  if (value === 'group' || value === 'platform') sortBy.value = value;
}

function statusTone(status: string): 'neutral' | 'accent' | 'success' | 'warning' | 'danger' {
  if (status === 'connected') return 'success';
  if (status === 'offline') return 'accent';
  if (status === 'error') return 'danger';
  return 'neutral';
}
const showAddDialog = ref(false);
const showQQDialog = ref(false);
const showNeteaseDialog = ref(false);
const showICloudDialog = ref(false);
const showSinaDialog = ref(false);
const showCustomDialog = ref(false);
const showEditDialog = ref(false);
const selectedProvider = ref('gmail');
const fetchHistory = ref(false);
const qqForm = ref({ email: '', auth_code: '' });
const neteaseForm = ref({ email: '', auth_code: '' });
const icloudForm = ref({ email: '', auth_code: '' });
const sinaForm = ref({ email: '', auth_code: '' });
const customForm = ref({
  email: '',
  username: '',
  auth_code: '',
  imap_host: '',
  imap_port: 993,
  imap_ssl: 'ssl',
  smtp_host: '',
  smtp_port: 465,
  smtp_ssl: 'ssl',
});
const MICROSOFT_ICON_SVG = '<svg width="24" height="24" viewBox="0 0 1024 1024"><path d="M0.10238 51.189762h460.503099v460.503099H0.10238V51.189762z" fill="#F45325"/><path d="M512.204759 51.189762H972.707858v460.503099h-460.503099V51.189762z" fill="#81BD06"/><path d="M0.10238 563.292142h460.503099v460.656668H0.10238v-460.656668z" fill="#04A6EF"/><path d="M512.204759 563.292142H972.707858v460.656668h-460.503099v-460.656668z" fill="#FFBA07"/></svg>';
const editingAccount = ref<any>(null);
const editForm = ref({ remark: '', group_name: '', hide_email: false, poll_interval_seconds: 10 });
const iconFileInput = ref<HTMLInputElement | null>(null);
const showIconPresets = ref(false);
const iconSaving = ref(false);
const cropSource = ref('');
const cropNaturalWidth = ref(0);
const cropNaturalHeight = ref(0);
const deleteJobs = ref<any[]>([]);
let deleteJobTimer: number | null = null;

const providers = [
  {
    type: 'gmail',
    name: 'Gmail',
    icon: '<svg width="24" height="24" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>',
  },
  {
    type: 'qq',
    name: 'QQ邮箱',
    icon: '<svg width="24" height="24" viewBox="0 0 1024 1024"><path d="M211.101867 363.776c-14.933333 66.56-7.466667 133.12 7.466666 192.256 14.933333 51.754667-7.466667 103.509333-52.309333 133.077333-67.285333 36.949333-149.461333-14.805333-156.970667-81.322666C-57.954133 260.266667 255.944533-57.642667 614.728533 8.874667c-209.28 22.186667-366.250667 162.688-403.626666 354.901333z" fill="#FFDC04"/><path d="M532.4672 844.373333c59.818667-22.186667 119.594667-59.136 164.437333-103.509333 37.376-36.992 97.152-44.373333 141.994667-14.805333 67.285333 36.992 67.285333 133.12 7.509333 177.493333-269.098667 229.162667-702.549333 118.272-822.186666-221.866667 112.128 162.688 321.408 221.866667 508.245333 162.688z" fill="#E03A22"/><path d="M794.056533 326.826667a425.173333 425.173333 0 0 0-171.861333-88.746667c-52.352-14.762667-89.728-59.136-89.728-110.933333 0-73.898667 82.218667-125.653333 149.504-96.085334 336.341333 118.314667 455.893333 539.733333 216.746667 813.312 89.685333-177.493333 37.376-391.850667-104.661334-517.546666z" fill="#27AA3A"/><path d="M652.104533 489.472c0-14.805333 0-29.568-7.509333-36.949333 0-7.424 0-7.424-7.466667-14.805334 0-73.941333-44.842667-133.12-127.061333-133.12-82.218667 0-127.061333 59.178667-127.061333 133.12 0 7.381333-7.466667 7.381333-7.466667 14.805334-7.466667 14.762667-7.466667 22.186667-7.466667 29.568v7.381333c-14.933333 7.381333-29.909333 29.568-37.376 51.754667-14.933333 36.949333-14.933333 73.941333-7.466666 73.941333 7.466667 7.381333 22.4-7.381333 37.333333-22.186667 0 22.186667 14.933333 44.373333 29.909333 59.136-14.933333 0-29.866667 14.805333-29.866666 29.568 0 22.186667 29.866667 36.992 74.709333 36.992 37.376 0 67.285333-14.805333 74.752-29.568h7.466667c7.466667 14.762667 37.376 29.568 74.752 29.568s74.752-14.805333 74.752-36.992c0-14.762667-14.933333-22.186667-29.909334-29.568 14.933333-14.762667 29.866667-29.568 37.376-51.754666 14.933333 22.186667 29.866667 29.568 37.376 22.186666 14.933333-7.381333 7.466667-36.949333-7.466667-73.941333-7.466667-22.186667-22.4-44.373333-37.376-51.754667v-7.381333z" fill="#2B2B2B"/></svg>',
  },
  {
    type: 'netease',
    name: '网易邮箱',
    icon: '<svg width="24" height="24" viewBox="0 0 1024 1024"><path d="M592.298667 661.76c60.458667-47.573333 67.072-49.92 84.992-27.392 15.573333 19.242667 12.245333 22.741333-91.733334 113.365333-34.688 30.592-63.744 62.293333-63.744 71.381334 0 7.936-8.96 14.762667-19.029333 14.762666-10.026667 0-46.933333 19.285333-81.493333 44.288C353.024 926.890667 227.84 981.333333 184.192 981.333333c-71.466667 0-67.072-71.381333 5.632-91.733333 124.117333-34.090667 251.605333-106.581333 402.432-227.84z m-46.848-200.618667c14.506667-5.717333 39.125333-7.978667 54.826666-5.589333 15.573333 1.109333 51.370667 5.674667 80.426667 9.045333 128.512 14.805333 224.64 132.693333 214.613333 259.626667-5.546667 70.229333-24.576 106.538667-81.578666 158.634667-89.514667 81.536-214.698667 121.216-257.109334 82.688-27.989333-26.112-50.304-81.706667-41.344-103.210667 5.546667-15.914667 10.069333-15.914667 41.344 1.152 70.4 36.266667 171.008-2.261333 229.12-87.296 58.154667-86.186667 33.493333-180.266667-46.933333-180.266667-29.056 0-40.234667-6.741333-51.370667-31.701333-21.333333-44.16-63.744-46.378667-111.829333-4.48-223.530667 196.053333-431.488 302.592-478.421333 245.930667-30.165333-36.224-6.741333-54.357333 117.333333-90.666667 42.538667-12.544 112.938667-49.834667 191.146667-103.168 111.786667-74.752 124.074667-86.058667 119.68-112.170667-4.522667-21.504 0-30.592 20.096-38.528z m-191.146667-410.282666c60.330667-12.458667 257.024-10.24 307.370667 3.328 95.061333 25.002667 110.634667 41.941333 138.666666 160 16.725333 70.272 15.616 101.973333-4.522666 150.698666-22.314667 55.594667-64.853333 69.12-201.216 68.010667-109.610667-1.109333-111.786667 0-130.816 29.44-23.509333 38.442667-118.570667 114.432-160.981334 130.346667-128.512 46.378667-200.106667 50.944-211.285333 14.677333-11.136-35.2 13.397333-56.704 66.005333-56.704 65.834667 0 174.336-44.245333 205.610667-82.773333 12.245333-13.568 4.437333-17.066667-48.085333-21.546667-70.4-5.589333-95.018667-28.330667-108.373334-99.712-12.245333-66.56 7.466667-125.738667 52.309334-147.242667 22.314667-10.24 74.922667-22.186667 119.765333-29.568 44.842667-5.674667 89.685333-22.186667 97.152-36.949333 7.466667-14.805333 29.866667-22.186667 52.309333-14.805333 22.314667 7.381333 52.309333 2.261333 67.242667-10.24 22.314667-19.242667 37.376-17.066667 52.309333 5.674666 14.933333 22.186667 44.842667 29.568 82.218667 22.186667z" fill="#C5161C"/></svg>',
  },
  {
    type: 'icloud',
    name: 'iCloud邮箱',
    icon: '<svg width="24" height="24" viewBox="0 0 1024 1024"><path d="M791.488 544.095c-1.28-129.695 105.76-191.871 110.528-194.975-60.16-88.032-153.856-100.064-187.232-101.472-79.744-8.064-155.584 46.944-196.064 46.944-40.352 0-102.816-45.76-168.96-44.544-86.912 1.28-167.072 50.528-211.808 128.384-90.304 156.703-23.136 388.831 64.896 515.935 43.008 62.208 94.304 132.064 161.632 129.568 64.832-2.592 89.376-41.952 167.744-41.952s100.416 41.952 169.056 40.672c69.76-1.312 113.984-63.392 156.704-125.792 49.376-72.16 69.728-142.048 70.912-145.632-1.536-0.704-136.064-52.224-137.408-207.136zM662.56 163.52C698.304 120.16 722.432 60 715.84 0c-51.488 2.112-113.888 34.304-150.816 77.536-33.152 38.368-62.144 99.616-54.368 158.432 57.472 4.48 116.128-29.216 151.904-72.448z" fill="currentColor"/></svg>',
  },
  {
    type: 'sina',
    name: '新浪邮箱',
    icon: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#e11d48" stroke-width="2"><path d="M4 6h16v12H4z"/><path d="m4 7 8 6 8-6"/></svg>',
  },
  {
    type: 'custom',
    name: '其他邮箱',
    icon: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 6-10 7L2 6"/></svg>',
  },
  {
    type: 'outlook',
    name: 'Microsoft',
    icon: MICROSOFT_ICON_SVG,
  },
];
const existingGroups = computed(() => {
  const groups = new Set<string>();
  mailStore.accounts.forEach(a => { if (a.group_name) groups.add(a.group_name); });
  return [...groups];
});

// 按分组或平台组织账号
const groupedAccounts = computed(() => {
  if (sortBy.value === 'platform') {
    const map = new Map<string, any[]>();
    mailStore.accounts.forEach(a => {
      const key = a.provider;
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(a);
    });
    return [...map.entries()].map(([key, accs]) => ({
      key,
      title: providerName(key),
      icon: providerIcon(key),
      accounts: accs,
    }));
  } else {
    const map = new Map<string, any[]>();
    mailStore.accounts.forEach(a => {
      const key = a.group_name || '未分组';
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(a);
    });
    return [...map.entries()].map(([key, accs]) => ({
      key,
      title: key,
      icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>',
      accounts: accs,
    }));
  }
});

async function handleOAuthSuccess() {
  sessionStorage.setItem('flymail_oauth_just_added', '1');
  showAddDialog.value = false;
  showEditDialog.value = false;
  fetchHistory.value = false;
  await mailStore.loadAccounts();
  await mailStore.loadFolders();
  mailStore.accounts.forEach((account: any) => {
    if (account.status !== 'offline') account.status = 'connected';
  });
}

function handleOAuthMessage(event: MessageEvent) {
  const data = event.data || {};
  if (data.type === 'flymail_oauth_success') {
    handleOAuthSuccess().catch((error) => {
      console.error('refresh accounts after OAuth failed:', error);
      ui.error('刷新账号列表失败');
    });
    return;
  }
  if (data.type === 'flymail_oauth_error') {
    ui.error(data.error || '邮箱授权失败');
  }
}

onMounted(async () => {
  window.addEventListener('message', handleOAuthMessage);
  connectWs();
  await mailStore.loadAccounts();
  await loadDeleteJobs();
  loading.value = false;
  // 立即将所有账号状态设为 checking，避免 sessionStorage 缓存的旧状态闪烁
  mailStore.accounts.forEach((account: any) => {
    if (account.status !== 'offline') account.status = 'checking';
  });
  const oauthJustAdded = sessionStorage.getItem('flymail_oauth_just_added') === '1';
  if (oauthJustAdded) {
    // OAuth 成功后后端还在启动同步/刷新令牌，立即测试容易把临时 token 状态误报成 invalid token。
    sessionStorage.removeItem('flymail_oauth_just_added');
    mailStore.accounts.forEach((account: any) => {
      if (account.status !== 'offline') account.status = 'connected';
    });
    return;
  }
  checkAllAccountsStatus();
  deleteJobTimer = window.setInterval(loadDeleteJobs, 2000);
});

onBeforeUnmount(() => {
  window.removeEventListener('message', handleOAuthMessage);
  if (deleteJobTimer) window.clearInterval(deleteJobTimer);
  clearCropSource();
});

async function checkAllAccountsStatus() {
  for (const account of mailStore.accounts) {
    if (account.status === 'offline') {
      mailStore.reauthAccountIds.delete(account.id);
      continue;
    }
    // 已知需要重新授权的账号跳过测试
    if (mailStore.reauthAccountIds.has(account.id)) {
      account.status = 'reauth_needed';
      continue;
    }
    account.status = 'checking';
  }
  await Promise.allSettled(
    mailStore.accounts.map(async (account) => {
      if (account.status === 'offline') return;
      if (account.status === 'reauth_needed') return;
      try {
        const data = await api.post(`/accounts/${account.id}/test`, {}, { timeout: 15000 }) as any;
        account.status = data.success ? 'connected' : 'error';
        if (!data.success) {
          console.warn('账号连接检测失败:', account.email, data.error || '未知错误');
        }
      } catch {
        account.status = 'error';
      } finally {
        if (account.status === 'checking') {
          account.status = 'error';
        }
      }
    })
  );
}

/** 启动邮箱认证流程：Gmail/Outlook 使用 OAuth 弹窗授权，QQ/网易/iCloud 使用授权码对话框 */
async function startAuth() {
  if (selectedProvider.value === 'qq') {
    showAddDialog.value = false;
    showQQDialog.value = true;
    return;
  }
  if (selectedProvider.value === 'netease') {
    showAddDialog.value = false;
    showNeteaseDialog.value = true;
    return;
  }
  if (selectedProvider.value === 'icloud') {
    showAddDialog.value = false;
    showICloudDialog.value = true;
    return;
  }
  if (selectedProvider.value === 'sina') {
    showAddDialog.value = false;
    showSinaDialog.value = true;
    return;
  }
  if (selectedProvider.value === 'custom') {
    showAddDialog.value = false;
    showCustomDialog.value = true;
    return;
  }
  const providerLabel = selectedProvider.value === 'outlook' ? 'Microsoft' : 'Google';
  const { win: authWindow } = openAuthWindowSync(providerLabel);
  if (!authWindow) {
    ui.error(authWindowBlockedMessage(providerLabel));
    return;
  }
  try {
    const settings = await api.get('/settings') as any;
    let redirectUri = '';
    if (selectedProvider.value === 'outlook') {
      redirectUri = settings.outlook_redirect_uri || '';
      if (!redirectUri) {
        closeAuthWindow(authWindow);
        ui.error('请先在设置页面配置 Microsoft 重定向 URI');
        return;
      }
    } else {
      redirectUri = settings.gmail_redirect_uri || '';
      if (!redirectUri) {
        closeAuthWindow(authWindow);
        ui.error('请先在设置页面配置 Gmail 重定向 URI');
        return;
      }
    }
    const data = await api.post('/accounts/auth-url', {
      provider: selectedProvider.value,
      redirect_uri: redirectUri,
      fetch_history: fetchHistory.value,
    }) as any;
    if (data.error) { closeAuthWindow(authWindow); ui.error('获取授权链接失败：' + data.error); return; }
    if (data.auth_url) {
      if (!navigateAuthWindow(authWindow, data.auth_url)) ui.error(authWindowBlockedMessage(providerLabel));
    } else { closeAuthWindow(authWindow); ui.error('获取授权链接失败'); }
  } catch (e: any) {
    closeAuthWindow(authWindow);
    ui.error('获取授权链接失败：' + (e.response?.data?.error || e.message || '网络错误'));
  }
}

async function addQQAccount() {
  if (!qqForm.value.email || !qqForm.value.auth_code) { ui.warning('请填写邮箱地址和授权码'); return; }
  try {
    const data = await api.post('/accounts/add-qq', { email: qqForm.value.email, auth_code: qqForm.value.auth_code, fetch_history: fetchHistory.value }, { timeout: 30000 }) as any;
    if (data.success) {
      ui.success('QQ邮箱添加成功！');
      showQQDialog.value = false;
      qqForm.value = { email: '', auth_code: '' };
      fetchHistory.value = false;
      await mailStore.loadAccounts();
      checkAllAccountsStatus();
    } else {
      ui.error('添加失败：' + (data.error || '未知错误'));
    }
  } catch (e: any) {
    ui.error('添加失败：' + (e.response?.data?.error || e.message || '网络错误'));
  }
}

async function addNeteaseAccount() {
  if (!neteaseForm.value.email || !neteaseForm.value.auth_code) { ui.warning('请填写邮箱地址和授权码'); return; }
  const email = neteaseForm.value.email.toLowerCase();
  const validSuffixes = ['@163.com', '@126.com', '@188.com', '@yeah.net'];
  if (!validSuffixes.some(s => email.endsWith(s))) {
    ui.warning('请输入163、126、188或yeah.net邮箱地址');
    return;
  }
  try {
    const data = await api.post('/accounts/add-netease', { email: neteaseForm.value.email, auth_code: neteaseForm.value.auth_code, fetch_history: fetchHistory.value }, { timeout: 30000 }) as any;
    if (data.success) {
      ui.success('网易邮箱添加成功！');
      showNeteaseDialog.value = false;
      neteaseForm.value = { email: '', auth_code: '' };
      fetchHistory.value = false;
      await mailStore.loadAccounts();
      checkAllAccountsStatus();
    } else {
      ui.error('添加失败：' + (data.error || '未知错误'));
    }
  } catch (e: any) {
    ui.error('添加失败：' + (e.response?.data?.error || e.message || '网络错误'));
  }
}

async function addICloudAccount() {
  if (!icloudForm.value.email || !icloudForm.value.auth_code) { ui.warning('请填写邮箱地址和应用专用密码'); return; }
  const email = icloudForm.value.email.toLowerCase();
  const validSuffixes = ['@icloud.com', '@me.com', '@mac.com'];
  if (!validSuffixes.some(s => email.endsWith(s))) {
    ui.warning('请输入icloud.com、me.com或mac.com邮箱地址');
    return;
  }
  try {
    const data = await api.post('/accounts/add-icloud', { email: icloudForm.value.email, auth_code: icloudForm.value.auth_code, fetch_history: fetchHistory.value }, { timeout: 30000 }) as any;
    if (data.success) {
      ui.success('iCloud邮箱添加成功！');
      showICloudDialog.value = false;
      icloudForm.value = { email: '', auth_code: '' };
      fetchHistory.value = false;
      await mailStore.loadAccounts();
      checkAllAccountsStatus();
    } else {
      ui.error('添加失败：' + (data.error || '未知错误'));
    }
  } catch (e: any) {
    ui.error('添加失败：' + (e.response?.data?.error || e.message || '网络错误'));
  }
}

async function addSinaAccount() {
  if (!sinaForm.value.email || !sinaForm.value.auth_code) { ui.warning('请填写邮箱地址和客户端授权码'); return; }
  const validSuffixes = ['@sina.com', '@sina.cn', '@2008.sina.com', '@vip.sina.com', '@vip.sina.cn'];
  if (!validSuffixes.some((suffix) => sinaForm.value.email.toLowerCase().endsWith(suffix))) {
    ui.warning('请输入新浪邮箱地址');
    return;
  }
  try {
    const data = await api.post('/accounts/add-sina', {
      email: sinaForm.value.email,
      auth_code: sinaForm.value.auth_code,
      fetch_history: fetchHistory.value,
    }, { timeout: 30000 }) as any;
    if (!data.success) throw new Error(data.error || '未知错误');
    ui.success('新浪邮箱添加成功！');
    showSinaDialog.value = false;
    sinaForm.value = { email: '', auth_code: '' };
    fetchHistory.value = false;
    await mailStore.loadAccounts();
    checkAllAccountsStatus();
  } catch (e: any) {
    ui.error('添加失败：' + (e.response?.data?.error || e.error || e.message || '网络错误'));
  }
}

function syncCustomUsername() {
  if (!customForm.value.username) customForm.value.username = customForm.value.email;
}

function updateCustomPort(protocol: 'imap' | 'smtp') {
  if (protocol === 'imap' && [143, 993].includes(Number(customForm.value.imap_port))) {
    customForm.value.imap_port = customForm.value.imap_ssl === 'ssl' ? 993 : 143;
  }
  if (protocol === 'smtp' && [465, 587].includes(Number(customForm.value.smtp_port))) {
    customForm.value.smtp_port = customForm.value.smtp_ssl === 'ssl' ? 465 : 587;
  }
}

function resetCustomForm() {
  customForm.value = {
    email: '', username: '', auth_code: '',
    imap_host: '', imap_port: 993, imap_ssl: 'ssl',
    smtp_host: '', smtp_port: 465, smtp_ssl: 'ssl',
  };
}

async function addCustomAccount() {
  try {
    const payload = {
      ...customForm.value,
      username: customForm.value.username || customForm.value.email,
      fetch_history: fetchHistory.value,
    };
    const data = await api.post('/accounts/add-custom', payload, { timeout: 60000 }) as any;
    if (!data.success) throw new Error(data.error || '未知错误');
    ui.success('其他邮箱添加成功！');
    showCustomDialog.value = false;
    resetCustomForm();
    fetchHistory.value = false;
    await mailStore.loadAccounts();
    checkAllAccountsStatus();
  } catch (e: any) {
    ui.error('添加失败：' + (e.response?.data?.error || e.error || e.message || '网络错误'));
  }
}

async function openReconnectDialog(account: any) {
  fetchHistory.value = false;
  if (account.provider === 'qq') {
    qqForm.value = { email: account.email || '', auth_code: '' };
    showQQDialog.value = true;
    return;
  }
  if (account.provider === 'netease') {
    neteaseForm.value = { email: account.email || '', auth_code: '' };
    showNeteaseDialog.value = true;
    return;
  }
  if (account.provider === 'icloud') {
    icloudForm.value = { email: account.email || '', auth_code: '' };
    showICloudDialog.value = true;
    return;
  }
  if (account.provider === 'sina') {
    sinaForm.value = { email: account.email || '', auth_code: '' };
    showSinaDialog.value = true;
    return;
  }
  if (account.provider === 'custom') {
    try {
      const config = await api.get(`/accounts/${account.id}/custom-config`) as any;
      customForm.value = {
        email: config.email || account.email || '',
        username: config.username || account.email || '',
        auth_code: '',
        imap_host: config.imap_host || '',
        imap_port: Number(config.imap_port || 993),
        imap_ssl: config.imap_ssl || 'ssl',
        smtp_host: config.smtp_host || '',
        smtp_port: Number(config.smtp_port || 465),
        smtp_ssl: config.smtp_ssl || 'ssl',
      };
      showCustomDialog.value = true;
    } catch (e: any) {
      ui.error(e?.error || e?.message || '读取邮箱配置失败');
    }
  }
}

function openEditDialog(account: any) {
  editingAccount.value = account;
  editForm.value = {
    remark: account.remark,
    group_name: account.group_name,
    hide_email: account.hide_email,
    poll_interval_seconds: account.poll_interval_seconds || 10,
  };
  showIconPresets.value = false;
  showEditDialog.value = true;
}

function clearCropSource() {
  if (cropSource.value) URL.revokeObjectURL(cropSource.value);
  cropSource.value = '';
  cropNaturalWidth.value = 0;
  cropNaturalHeight.value = 0;
}

function closeCropDialog() {
  if (!iconSaving.value) clearCropSource();
}

function closeEditDialog() {
  if (iconSaving.value) return;
  clearCropSource();
  showIconPresets.value = false;
  showEditDialog.value = false;
}

function reselectIconFile() {
  closeCropDialog();
  iconFileInput.value?.click();
}

function applyIconResponse(accountId: string, data: any) {
  mailStore.patchAccount(accountId, {
    icon_type: data.icon_type || 'default',
    icon_value: data.icon_value || '',
    icon_url: data.icon_url || '',
  });
  editingAccount.value = mailStore.accounts.find((account) => account.id === accountId) || editingAccount.value;
}

async function selectAccountIconPreset(presetId: AccountIconPresetId) {
  if (!editingAccount.value || iconSaving.value) return;
  iconSaving.value = true;
  try {
    const accountId = editingAccount.value.id;
    const data = await api.put(`/accounts/${accountId}/icon/preset`, { preset_id: presetId }) as any;
    applyIconResponse(accountId, data);
    showIconPresets.value = false;
    ui.success('邮箱图标已更新');
  } catch (error: any) {
    ui.error(error?.error || error?.message || '更新邮箱图标失败');
  } finally {
    iconSaving.value = false;
  }
}

async function resetAccountIcon() {
  if (!editingAccount.value || iconSaving.value) return;
  iconSaving.value = true;
  try {
    const accountId = editingAccount.value.id;
    const data = await api.delete(`/accounts/${accountId}/icon`) as any;
    applyIconResponse(accountId, data);
    showIconPresets.value = false;
    ui.success('已恢复服务商默认图标');
  } catch (error: any) {
    ui.error(error?.error || error?.message || '恢复默认图标失败');
  } finally {
    iconSaving.value = false;
  }
}

async function selectIconFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (!file) return;
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
    ui.error('仅支持 JPG、PNG 或 WebP 图片');
    return;
  }
  if (file.size > 10 * 1024 * 1024) {
    ui.error('图片不能超过 10 MB');
    return;
  }

  clearCropSource();
  const objectUrl = URL.createObjectURL(file);
  const image = new Image();
  image.src = objectUrl;
  try {
    await image.decode();
    if (image.naturalWidth * image.naturalHeight > 40_000_000) {
      URL.revokeObjectURL(objectUrl);
      ui.error('图片尺寸过大，请更换图片');
      return;
    }
    cropNaturalWidth.value = image.naturalWidth;
    cropNaturalHeight.value = image.naturalHeight;
    cropSource.value = objectUrl;
  } catch {
    URL.revokeObjectURL(objectUrl);
    ui.error('无法读取该图片，请更换文件');
  }
}

async function uploadCroppedIcon(blob: Blob) {
  if (!editingAccount.value || iconSaving.value) return;
  iconSaving.value = true;
  try {
    const accountId = editingAccount.value.id;
    const body = new FormData();
    body.append('icon', blob, 'account-icon.webp');
    const data = await api.post(`/accounts/${accountId}/icon/upload`, body, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }) as any;
    applyIconResponse(accountId, data);
    clearCropSource();
    ui.success('邮箱图标已更新');
  } catch (error: any) {
    ui.error(error?.error || error?.message || '上传邮箱图标失败');
  } finally {
    iconSaving.value = false;
  }
}

async function saveEdit() {
  if (!editingAccount.value) return;
  try {
    editForm.value.poll_interval_seconds = Math.min(3600, Math.max(5, Number(editForm.value.poll_interval_seconds) || 10));
    await api.put(`/accounts/${editingAccount.value.id}`, editForm.value);
    mailStore.patchAccount(editingAccount.value.id, {
      remark: editForm.value.remark,
      group_name: editForm.value.group_name,
      hide_email: editForm.value.hide_email,
      poll_interval_seconds: editForm.value.poll_interval_seconds,
    });
    editingAccount.value = mailStore.accounts.find((account) => account.id === editingAccount.value.id) || editingAccount.value;
    showEditDialog.value = false;
    ui.success('保存成功');
  } catch {
    ui.error('保存失败');
  }
}

async function loadDeleteJobs() {
  try {
    const data = await api.get('/accounts/delete-jobs') as any;
    const previous = deleteJobs.value;
    deleteJobs.value = data.jobs || [];
    const hadActive = previous.some((job: any) => job.status === 'pending' || job.status === 'running');
    const hasActive = deleteJobs.value.some((job: any) => job.status === 'pending' || job.status === 'running');
    if (hadActive && !hasActive) {
      await mailStore.loadAccounts();
      if (mailStore.currentAccountId) await mailStore.loadFolders();
    }
  } catch (e) {
    console.error('加载删除进度失败:', e);
  }
}

function deleteJobPercent(job: any) {
  const total = Math.max(Number(job.total_folders || 0), 1);
  const done = Math.max(Number(job.completed_folders || 0), 0);
  return Math.min(100, Math.round((done / total) * 100));
}

function deleteJobText(job: any) {
  if (job.status === 'failed') return job.error_message || '删除失败';
  if (job.status === 'completed') return '删除完成';
  return `${job.completed_folders || 0} / ${job.total_folders || 0}`;
}

async function confirmDisable(account: any) {
  const ok = await ui.showConfirm({
    title: '禁用账户',
    message: `确定要禁用 ${account.email} 吗？本地邮件和附件都会保留，授权也会保留；禁用后后台同步、实时刷新、手动刷新和查询都只使用本地缓存。`,
    confirmText: '确认禁用',
    danger: true,
  });
  if (!ok) return;
  try {
    await api.post(`/accounts/${account.id}/disable`);
    showEditDialog.value = false;
    await mailStore.loadAccounts();
    await mailStore.loadFolders();
    ui.success('账户已禁用');
  } catch (e: any) {
    ui.error(e?.message || e?.error || e?.response?.data?.error || '禁用失败');
  }
}

async function confirmDelete(account: any) {
  const ok = await ui.showConfirm({
    title: '删除账户',
    message: `确定要删除 ${account.email} 吗？本地缓存的邮件、附件和图片都会被删除。这个过程可能比较慢，会在账户列表显示删除进度。`,
    confirmText: '确认删除',
    danger: true,
  });
  if (ok) {
    try {
      await api.delete(`/accounts/${account.id}`);
      mailStore.clearCurrentAccountState();
      await mailStore.loadAccounts();
      if (mailStore.currentAccountId) await mailStore.loadFolders();
      await loadDeleteJobs();
      showEditDialog.value = false;
      ui.success('已开始后台删除');
    } catch (e: any) {
      ui.error(e?.message || e?.error || e?.response?.data?.error || '删除失败');
    }
  }
}


function statusText(status: string) {
  const map: Record<string, string> = {
    connected: '已连接',
    disconnected: '未连接',
    offline: '离线可查看',
    error: '连接异常',
    checking: '检测中',
    reauth_needed: '需要重新授权',
  };
  return map[status] || status;
}

/** 重新授权指定账号（复用添加账号的 OAuth 流程） */
async function reconnectAccount(account: any) {
  if (!account) return;
  if (account.status === 'offline') {
    try {
      await api.post(`/accounts/${account.id}/enable`);
      showEditDialog.value = false;
      await mailStore.loadAccounts();
      await mailStore.loadFolders();
      checkAllAccountsStatus();
      ui.success('账户已启用');
    } catch (e: any) {
      ui.error(e?.message || e?.error || e?.response?.data?.error || '启用失败');
    }
    return;
  }
  if (['qq', 'netease', 'icloud', 'sina', 'custom'].includes(account.provider)) {
    await openReconnectDialog(account);
    return;
  }
  const providerLabel = account.provider === 'outlook' ? 'Microsoft' : 'Google';
  const { win: authWindow } = openAuthWindowSync(providerLabel);
  if (!authWindow) {
    ui.error(authWindowBlockedMessage(providerLabel));
    return;
  }
  try {
    const settingsData = await api.get('/settings') as any;
    const settings = settingsData.settings || settingsData || {};
    const redirectUri = account.provider === 'outlook'
      ? (settings.outlook_redirect_uri || '')
      : (settings.gmail_redirect_uri || '');
    if (!redirectUri) {
      closeAuthWindow(authWindow);
      ui.error(account.provider === 'outlook' ? '请先在设置页面配置 Microsoft 重定向 URI' : '请先在设置页面配置 Gmail 重定向 URI');
      return;
    }
    const data = await api.post('/accounts/auth-url', {
      provider: account.provider,
      redirect_uri: redirectUri,
    }) as any;
    if (data.auth_url) {
      if (!navigateAuthWindow(authWindow, data.auth_url)) ui.error(authWindowBlockedMessage(providerLabel));
    } else {
      closeAuthWindow(authWindow);
      ui.error('获取授权链接失败');
    }
  } catch (e) {
    closeAuthWindow(authWindow);
    ui.error('获取授权链接失败');
  }
}
</script>

<style scoped>
.account-page {
  width: 100%;
  min-width: 0;
  min-height: 0;
  background: var(--bg-secondary);
}

.sort-toggle {
  display: flex;
  background: var(--bg-tertiary);
  border-radius: var(--border-radius-full);
  padding: 3px;
  gap: 2px;
}

.toggle-btn {
  padding: 6px 18px;
  border: none;
  border-radius: var(--border-radius-full);
  background: transparent;
  color: var(--text-tertiary);
  font-size: var(--text-xs);
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  transition: all var(--transition-fast);
  white-space: nowrap;
}

.toggle-btn.active {
  background: var(--bg-primary);
  color: var(--text-primary);
  box-shadow: var(--ui-shadow-xs);
}

/* ==================== 分组区域 ==================== */
.account-sections {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.delete-jobs {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.delete-job {
  padding: var(--space-3) var(--space-4);
  border: 1px solid color-mix(in srgb, var(--ui-danger) 28%, var(--ui-border));
  border-radius: var(--ui-radius-sm);
  background: var(--ui-danger-soft);
}

.delete-job-main {
  display: flex;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-2);
  color: var(--text-primary);
  font-size: var(--text-sm);
}

.delete-job-title { font-weight: 600; }
.delete-job-meta { color: var(--text-secondary); }

.delete-progress {
  height: 6px;
  border-radius: var(--ui-radius-round);
  background: color-mix(in srgb, var(--ui-danger) 22%, transparent);
  overflow: hidden;
}

.delete-progress-bar {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--ui-danger);
  transition: width var(--ui-motion-fast);
}

.section-header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
  padding: 0 var(--space-1);
}

.section-icon {
  display: flex;
  align-items: center;
  color: var(--text-secondary);
}

.section-title {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--text-secondary);
}

.section-count {
  font-size: 11px;
  color: var(--text-tertiary);
  background: var(--bg-tertiary);
  padding: 1px 7px;
  border-radius: var(--border-radius-full);
  font-weight: 500;
}

/* ==================== 账号列表 ==================== */
.account-list,
.account-card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: var(--ui-space-3);
}

/* 账号卡片 */
.account-card {
  min-width: 0;
  min-height: 88px;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--ui-space-4);
  border: 1px solid var(--ui-border);
  background: var(--bg-card);
  border-radius: var(--ui-radius-lg);
  box-shadow: var(--shadow-card);
  cursor: pointer;
  transition: all 0.2s ease;
}

.account-card:hover {
  box-shadow: var(--shadow-md);
  background: var(--bg-hover);
}

/* 账号信息 */
.account-info {
  flex: 1;
  min-width: 0;
}

.info-main {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  overflow: hidden;
}

.account-name {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.name-remark {
  color: var(--text-primary);
}

.name-email {
  color: var(--text-primary);
}

.account-email-sub {
  font-size: var(--text-xs);
  color: var(--text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex-shrink: 1;
  min-width: 0;
}

.info-meta {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 2px;
}

.meta-provider {
  font-size: 11px;
  color: var(--text-tertiary);
}

.meta-sep {
  font-size: 11px;
  color: var(--border-color);
}

/* 编辑按钮 */
.edit-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: var(--border-radius-sm);
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  flex-shrink: 0;
  transition: all var(--transition-fast);
  opacity: 0;
}

.account-card:hover .edit-btn {
  opacity: 1;
}

.edit-btn:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

/* ==================== 状态标签 ==================== */
.account-status {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  font-weight: 500;
  padding: 1px 7px;
  border-radius: var(--border-radius-full);
}

.status-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
}

.account-status.connected { background: var(--ui-success-soft); color: var(--ui-success); }
.account-status.connected .status-dot { background: var(--ui-success); }
.account-status.disconnected { background: var(--bg-tertiary); color: var(--text-tertiary); }
.account-status.disconnected .status-dot { background: var(--text-tertiary); }
.account-status.offline { background: var(--ui-accent-soft); color: var(--ui-accent); }
.account-status.offline .status-dot { background: var(--ui-accent); }
.account-status.error { background: var(--ui-danger-soft); color: var(--ui-danger); }
.account-status.error .status-dot { background: var(--ui-danger); }
.account-status.checking { background: var(--ui-accent-soft); color: var(--ui-accent); }
.account-status.checking .status-dot { background: var(--ui-accent); animation: status-pulse 0.8s ease-in-out infinite; }
.account-status.reauth_needed { background: var(--ui-warning-soft); color: var(--ui-warning); }
.account-status.reauth_needed .status-dot { background: var(--ui-warning); }

@keyframes status-pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.4; transform: scale(0.7); }
}

/* 操作按钮区 */
.card-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

/* 重新授权按钮 */
.btn-reauth-card {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  background: var(--ui-warning-soft);
  border: 1px solid color-mix(in srgb, var(--ui-warning) 34%, var(--ui-border));
  border-radius: var(--border-radius-sm);
  color: var(--ui-warning);
  font-size: 11px;
  font-weight: 500;
  cursor: pointer;
  transition: all var(--transition-fast);
  white-space: nowrap;
}

.btn-reauth-card:hover {
  background: color-mix(in srgb, var(--ui-warning) 22%, transparent);
}

/* ==================== 对话框 ==================== */
.dialog-desc {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  margin-bottom: var(--space-5);
  margin-top: calc(var(--space-1) * -1);
}

.provider-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-3);
  margin-bottom: var(--space-2);
}

.provider-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-4) var(--space-3);
  border: 2px solid var(--border-color);
  border-radius: var(--border-radius-md);
  background: var(--bg-primary);
  cursor: pointer;
  transition: all var(--transition-fast);
  font-family: inherit;
}

.provider-card:hover { border-color: var(--border-color-strong); background: var(--bg-hover); }
.provider-card.active { border-color: var(--color-accent); background: var(--color-accent-lighter); }

.provider-icon { display: flex; align-items: center; justify-content: center; }
.provider-name { font-size: var(--text-sm); font-weight: 500; color: var(--text-primary); }

/* 编辑表单 */
.edit-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.group-tags {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
  margin-top: var(--space-1);
}

.group-tag {
  padding: 3px 10px;
  border: 1px solid var(--border-color);
  border-radius: var(--border-radius-full);
  background: var(--bg-primary);
  color: var(--text-secondary);
  font-size: var(--text-xs);
  font-family: inherit;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.group-tag:hover { border-color: var(--color-accent); color: var(--color-accent); }
.group-tag.active { border-color: var(--color-accent); background: var(--color-accent-lighter); color: var(--color-accent); }

.account-icon-editor {
  padding: var(--ui-space-4);
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-md);
  background: var(--ui-surface-2);
}

.account-icon-editor__current {
  display: flex;
  align-items: center;
  gap: var(--ui-space-4);
}

.account-icon-editor__actions {
  min-width: 0;
  display: flex;
  flex-wrap: wrap;
  gap: var(--ui-space-2);
}

.account-icon-file-input {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  clip-path: inset(50%);
  white-space: nowrap;
}

.account-icon-preset-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--ui-space-2);
  margin-top: var(--ui-space-2);
}

.account-icon-preset {
  min-width: 0;
  display: grid;
  justify-items: center;
  gap: var(--ui-space-2);
  padding: var(--ui-space-3) var(--ui-space-2);
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-sm);
  background: var(--ui-surface-1);
  color: var(--ui-text-2);
  cursor: pointer;
}

.account-icon-preset:hover,
.account-icon-preset.active {
  border-color: var(--ui-accent);
  background: var(--ui-accent-soft);
  color: var(--ui-text-1);
}

.account-icon-preset:focus-visible {
  outline: 3px solid var(--ui-focus-ring);
  outline-offset: 1px;
}

.account-icon-preset:disabled { opacity: 0.55; cursor: not-allowed; }
.account-icon-preset > span,
.account-icon-preset > span :deep(svg) { width: 32px; height: 32px; display: block; }
.account-icon-preset small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; }

/* 隐藏邮箱 toggle */
.toggle-field {
  display: flex !important;
  flex-direction: row !important;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-2) 0;
}

.toggle-label {
  font-size: var(--text-sm);
  color: var(--text-primary);
}

.toggle-switch {
  width: 48px;
  height: 28px;
  border-radius: 14px;
  border: none;
  background: var(--bg-tertiary);
  position: relative;
  cursor: pointer;
  transition: background var(--transition-fast);
  flex-shrink: 0;
}

.toggle-switch.active { background: var(--color-accent); }

.toggle-knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--ui-text-inverse);
  transition: transform var(--transition-fast);
  box-shadow: var(--ui-shadow-xs);
}

.toggle-switch.active .toggle-knob { transform: translateX(20px); }

/* 授权码与通用邮箱表单 */
.qq-form { display: flex; flex-direction: column; gap: var(--space-4); }
.dialog-wide { width: min(720px, calc(100vw - 32px)); }
.custom-form { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: var(--space-4); }
.custom-span-2 { grid-column: 1 / -1; }
.form-section { font-size: var(--text-sm); font-weight: 700; color: var(--text-primary); padding-top: var(--space-2); border-top: 1px solid var(--border-color); }
.server-row { display: grid; grid-template-columns: minmax(90px, 0.8fr) minmax(130px, 1.2fr); gap: var(--space-3); }
.form-field { display: flex; flex-direction: column; gap: var(--space-2); }
.field-label { font-size: var(--text-sm); font-weight: 500; color: var(--text-primary); }
.field-hint { font-size: var(--text-xs); color: var(--text-tertiary); margin-top: var(--space-1); }
.hint-link { color: var(--color-accent); text-decoration: none; }
.hint-link:hover { text-decoration: underline; }

.btn-danger-text { color: var(--color-danger) !important; }
.btn-danger-text:hover { background: var(--color-danger-light) !important; }

/* ==================== 移动端适配 ==================== */
@media (max-width: 768px) {
  .sort-toggle { width: 100%; justify-content: center; }
  .toggle-btn { flex: 1; text-align: center; padding: 8px 16px; }
  .provider-grid { grid-template-columns: 1fr; }
  .custom-form { grid-template-columns: 1fr; }
  .custom-span-2 { grid-column: auto; }
  .server-row { grid-template-columns: 1fr 1fr; }
  .edit-btn { opacity: 1; }
  .account-icon-editor__current { align-items: flex-start; }
  .account-icon-preset-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>

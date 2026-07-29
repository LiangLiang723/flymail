<template>
  <section class="notify-page ui-page">
    <header>
      <div>
        <h2>第三方通知</h2>
        <p>将新邮件通知发送到 Bark、Telegram 或兼容 Webhook。</p>
      </div>
      <button class="btn btn-primary" type="button" :disabled="saving" @click="save">
        {{ saving ? '保存中…' : '保存设置' }}
      </button>
    </header>

    <div v-if="loading" class="state-card">正在加载通知设置…</div>
    <template v-else>
      <div class="card overview-card">
        <label class="toggle-row">
          <span><strong>启用第三方通知</strong><small>应用内通知不受此开关影响。</small></span>
          <input v-model="form.enabled" type="checkbox" />
        </label>
        <div class="grid three">
          <label class="field"><span>通知模式</span><select v-model="form.mode"><option value="text">文本</option><option value="image">图片卡片</option></select></label>
          <label class="field"><span>免打扰开始</span><input v-model="form.dnd_start" type="time" /></label>
          <label class="field"><span>免打扰结束</span><input v-model="form.dnd_end" type="time" /></label>
        </div>
      </div>

      <div class="card">
        <div class="card-title"><div><strong>Bark</strong><small>适用于 iPhone / iPad 的 Bark 推送。</small></div><input v-model="form.bark.enabled" type="checkbox" /></div>
        <div class="grid two">
          <label class="field"><span>Bark 服务地址</span><input v-model.trim="form.bark.server" placeholder="https://api.day.app" /></label>
          <label class="field"><span>Device Key</span><input v-model.trim="form.bark.device_key" type="password" autocomplete="new-password" /></label>
        </div>
        <button class="btn btn-secondary" type="button" :disabled="testing === 'bark'" @click="testChannel('bark')">测试 Bark</button>
      </div>

      <div class="card">
        <div class="card-title"><div><strong>Telegram</strong><small>通过机器人向指定 Chat ID 推送。</small></div><input v-model="form.telegram.enabled" type="checkbox" /></div>
        <div class="grid two">
          <label class="field"><span>Bot Token</span><input v-model.trim="form.telegram.bot_token" type="password" autocomplete="new-password" /></label>
          <label class="field"><span>Chat ID</span><input v-model.trim="form.telegram.chat_id" /></label>
        </div>
        <label class="check-row"><input v-model="form.telegram.use_gmail_proxy" type="checkbox" /> 使用 Gmail 代理配置访问 Telegram</label>
        <button class="btn btn-secondary" type="button" :disabled="testing === 'telegram'" @click="testChannel('telegram')">测试 Telegram</button>
      </div>

      <div class="card">
        <div class="card-title"><div><strong>Webhook</strong><small>自动适配企业微信、钉钉、飞书和通用 JSON Webhook。</small></div><input v-model="form.webhook.enabled" type="checkbox" /></div>
        <div class="grid two">
          <label class="field"><span>Webhook URL</span><input v-model.trim="form.webhook.url" placeholder="https://..." /></label>
          <label class="field"><span>密钥 / Secret（可选）</span><input v-model.trim="form.webhook.secret" type="password" autocomplete="new-password" /></label>
        </div>
        <label class="check-row"><input v-model="form.webhook.use_gmail_proxy" type="checkbox" /> 使用 Gmail 代理配置访问 Webhook</label>
        <button class="btn btn-secondary" type="button" :disabled="testing === 'webhook'" @click="testChannel('webhook')">测试 Webhook</button>
      </div>

      <div class="card">
        <div class="card-title"><div><strong>通知图片图床</strong><small>图片模式下，Bark、钉钉和飞书需要公网图片地址。</small></div></div>
        <div class="grid two">
          <label class="field"><span>图床地址</span><input v-model.trim="form.imgbed.base_url" placeholder="https://your-worker.workers.dev" /></label>
          <label class="field"><span>上传密钥</span><input v-model.trim="form.imgbed.upload_token" type="password" autocomplete="new-password" /></label>
        </div>
        <div class="actions">
          <button class="btn btn-secondary" type="button" :disabled="testing === 'imgbed'" @click="testImgbed">测试图床</button>
          <button class="btn btn-secondary danger" type="button" :disabled="testing === 'purge'" @click="purgeImgbed">清理图床图片</button>
          <a v-if="deployUrl" class="deploy-link" :href="deployUrl" target="_blank" rel="noopener noreferrer">部署 Cloudflare 图床模板</a>
        </div>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import api from '../utils/api';
import { useUIStore } from '../stores/ui';

const ui = useUIStore();
const loading = ref(true);
const saving = ref(false);
const testing = ref('');
const deployUrl = ref('');

const form = reactive({
  enabled: false,
  dnd_start: '21:00',
  dnd_end: '07:00',
  mode: 'text',
  bark: { enabled: false, server: 'https://api.day.app', device_key: '' },
  telegram: { enabled: false, bot_token: '', chat_id: '', use_gmail_proxy: false },
  webhook: { enabled: false, url: '', secret: '', use_gmail_proxy: false },
  imgbed: { base_url: '', upload_token: '' },
});

function applySettings(data: any) {
  form.enabled = Boolean(data?.enabled);
  form.dnd_start = data?.dnd_start || '21:00';
  form.dnd_end = data?.dnd_end || '07:00';
  form.mode = data?.mode === 'image' ? 'image' : 'text';
  Object.assign(form.bark, data?.bark || {});
  Object.assign(form.telegram, data?.telegram || {});
  Object.assign(form.webhook, data?.webhook || {});
  Object.assign(form.imgbed, data?.imgbed || {});
  deployUrl.value = data?.imgbed_deploy_url || '';
}

async function load() {
  loading.value = true;
  try {
    const result = await api.get('/notify/settings') as any;
    applySettings(result.data || {});
  } catch (error: any) {
    ui.error(error?.error || error?.message || '加载通知设置失败');
  } finally {
    loading.value = false;
  }
}

async function save() {
  saving.value = true;
  try {
    const result = await api.put('/notify/settings', JSON.parse(JSON.stringify(form))) as any;
    applySettings(result.data || form);
    ui.success(result.message || '通知设置已保存');
  } catch (error: any) {
    ui.error(error?.error || error?.message || '保存通知设置失败');
  } finally {
    saving.value = false;
  }
}

async function testChannel(channel: 'bark' | 'telegram' | 'webhook') {
  testing.value = channel;
  try {
    await save();
    const result = await api.post('/notify/test', { channel }) as any;
    result.success ? ui.success(result.message || '测试消息已发送') : ui.error(result.message || '测试失败');
  } catch (error: any) {
    ui.error(error?.error || error?.message || '测试失败');
  } finally {
    testing.value = '';
  }
}

async function testImgbed() {
  testing.value = 'imgbed';
  try {
    const result = await api.post('/notify/imgbed/test', { imgbed: form.imgbed }) as any;
    result.success ? ui.success(result.data?.url ? `图床测试成功：${result.data.url}` : result.message) : ui.error(result.message || '图床测试失败');
  } catch (error: any) {
    ui.error(error?.error || error?.message || '图床测试失败');
  } finally {
    testing.value = '';
  }
}

async function purgeImgbed() {
  const confirmed = await ui.showConfirm({ title: '清理图床', message: '确定删除该图床中的全部 FlyMail 通知图片吗？', confirmText: '确认清理', danger: true });
  if (!confirmed) return;
  testing.value = 'purge';
  try {
    const result = await api.post('/notify/imgbed/purge', { imgbed: form.imgbed }) as any;
    result.success ? ui.success(result.message || '清理完成') : ui.error(result.message || '清理失败');
  } catch (error: any) {
    ui.error(error?.error || error?.message || '清理失败');
  } finally {
    testing.value = '';
  }
}

onMounted(load);
</script>

<style scoped>
.notify-page { height: 100%; overflow-y: auto; padding: 24px; background: var(--ui-canvas); }
header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 18px; }
h2 { margin: 0 0 6px; } header p { margin: 0; color: var(--ui-text-3); }
.card, .state-card { max-width: 920px; margin: 0 auto 16px; padding: 18px; border: 1px solid var(--ui-border); border-radius: 12px; background: var(--ui-surface-1); }
.overview-card { margin-top: 0; }.card-title, .toggle-row { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 14px; }.card-title div, .toggle-row span { display: flex; flex-direction: column; gap: 4px; }.card small, .toggle-row small { color: var(--ui-text-3); font-weight: 400; }.grid { display: grid; gap: 14px; margin-bottom: 14px; }.grid.two { grid-template-columns: repeat(2, minmax(0, 1fr)); }.grid.three { grid-template-columns: repeat(3, minmax(0, 1fr)); }.field { display: flex; flex-direction: column; gap: 6px; }.field span { font-size: 13px; font-weight: 600; }.field input, .field select { width: 100%; padding: 9px 10px; border: 1px solid var(--ui-border-strong); border-radius: 8px; background: var(--ui-surface-1); color: var(--ui-text-1); box-sizing: border-box; }.check-row { display: flex; align-items: center; gap: 8px; margin: 0 0 14px; color: var(--ui-text-2); }.actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }.deploy-link { color: var(--ui-accent); text-decoration: none; }.danger { color: var(--ui-danger); }.state-card { display: flex; justify-content: center; min-height: 140px; align-items: center; color: var(--ui-text-3); }
@media (max-width: 760px) { .notify-page { padding: 12px; } header { flex-direction: column; }.grid.two, .grid.three { grid-template-columns: 1fr; } }
</style>

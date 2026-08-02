<script setup lang="ts">
import { computed, onMounted, reactive } from 'vue';

import { apiClient } from '../../shared/api/client.ts';
import { normalizeApiError } from '../../shared/api/errors.ts';
import { configuredSecret } from './notification-state.ts';

type ChannelKey = 'bark' | 'telegram' | 'wecom' | 'dingtalk' | 'feishu' | 'generic_webhook';
type EventType = 'mail.new' | 'send.failed' | 'backup.completed' | 'sync.failed';
type PublisherKey = 'flymail_imgbed' | 'generic_https';

interface Channel {
  id: string;
  channel_key: ChannelKey;
  display_name: string;
  enabled: boolean;
  public_config: Record<string, string | number | boolean>;
  secret_configured: boolean;
  use_proxy: boolean;
}
interface Rule {
  id: string;
  event_type: EventType;
  channel_id: string;
  image_publisher_id: string | null;
  enabled: boolean;
  use_proxy: boolean;
  dedupe_window_seconds: number;
}
interface Publisher {
  id: string;
  publisher_key: PublisherKey;
  display_name: string;
  endpoint_url: string;
  enabled: boolean;
  public_config: Record<string, string | number | boolean>;
  secret_configured: boolean;
}

const state = reactive<{ channels: Channel[]; rules: Rule[]; publishers: Publisher[]; error: string; testResult: string }>({
  channels: [], rules: [], publishers: [], error: '', testResult: '',
});
const channel = reactive({ channel_key: 'bark' as ChannelKey, display_name: '', endpoint_url: '', token: '', chat_id: '', enabled: true, use_proxy: false });
const rule = reactive({ event_type: 'mail.new' as EventType, channel_id: '', image_publisher_id: '', enabled: true, use_proxy: false, dedupe_window_seconds: 0 });
const publisher = reactive({ publisher_key: 'flymail_imgbed' as PublisherKey, display_name: 'flymail-imgbed', endpoint_url: '', token: '', enabled: true });
const endpointRequired = computed(() => ['wecom', 'dingtalk', 'feishu', 'generic_webhook'].includes(channel.channel_key));

function channelBody(item: typeof channel | Channel, secret: Record<string, string> = {}) {
  const endpoint = 'endpoint_url' in item
    ? String(item.endpoint_url || '')
    : String(item.public_config.endpoint_url || '');
  const chatId = 'chat_id' in item
    ? String(item.chat_id || '')
    : String(item.public_config.chat_id || '');
  return {
    channel_key: item.channel_key,
    display_name: item.display_name,
    enabled: item.enabled,
    public_config: { ...(endpoint ? { endpoint_url: endpoint } : {}), ...(chatId ? { chat_id: chatId } : {}) },
    secret,
    use_proxy: item.use_proxy,
  };
}
function ruleBody(item: typeof rule | Rule) {
  return {
    event_type: item.event_type,
    channel_id: item.channel_id,
    image_publisher_id: item.image_publisher_id || null,
    enabled: item.enabled,
    use_proxy: item.use_proxy,
    dedupe_window_seconds: Number(item.dedupe_window_seconds || 0),
  };
}
function publisherBody(item: typeof publisher | Publisher, secret: Record<string, string> = {}) {
  return {
    publisher_key: item.publisher_key,
    display_name: item.display_name,
    endpoint_url: item.endpoint_url,
    enabled: item.enabled,
    public_config: {},
    secret,
  };
}
async function load() {
  try {
    const [channels, rules, publishers] = await Promise.all([
      apiClient.request<{ items: Channel[] }>({ method: 'GET', path: '/api/v2/notification-channels' }),
      apiClient.request<Rule[]>({ method: 'GET', path: '/api/v2/notification-rules' }),
      apiClient.request<Publisher[]>({ method: 'GET', path: '/api/v2/notification-publishers' }),
    ]);
    state.channels = channels.items;
    state.rules = rules;
    state.publishers = publishers;
    if (!rule.channel_id && channels.items[0]) rule.channel_id = channels.items[0].id;
  } catch (value: unknown) {
    state.error = normalizeApiError(value).message;
  }
}
async function saveChannel() {
  state.error = '';
  try {
    await apiClient.request({ method: 'POST', path: '/api/v2/notification-channels', body: channelBody(channel, channel.token ? { token: channel.token } : {}) });
    channel.token = ''; channel.endpoint_url = ''; channel.chat_id = ''; channel.display_name = '';
    await load();
  } catch (value: unknown) { state.error = normalizeApiError(value).message; }
}
async function toggleChannel(item: Channel) {
  await apiClient.request({ method: 'PUT', path: `/api/v2/notification-channels/${encodeURIComponent(item.id)}`, body: channelBody({ ...item, enabled: !item.enabled }) });
  await load();
}
async function deleteChannel(item: Channel) {
  if (!window.confirm(`删除通知渠道“${item.display_name}”？`)) return;
  await apiClient.request({ method: 'DELETE', path: `/api/v2/notification-channels/${encodeURIComponent(item.id)}` });
  await load();
}
async function testChannel(item: Channel) {
  const response = await apiClient.request<{ task_id: string; status: string }>({ method: 'POST', path: `/api/v2/notification-channels/${encodeURIComponent(item.id)}/test` });
  state.testResult = `${item.display_name}: ${response.status} · ${response.task_id}`;
}
async function saveRule() {
  if (!rule.channel_id) { state.error = '请先创建并选择通知渠道'; return; }
  await apiClient.request({ method: 'POST', path: '/api/v2/notification-rules', body: ruleBody(rule) });
  await load();
}
async function toggleRule(item: Rule) {
  await apiClient.request({ method: 'PUT', path: `/api/v2/notification-rules/${encodeURIComponent(item.id)}`, body: ruleBody({ ...item, enabled: !item.enabled }) });
  await load();
}
async function deleteRule(item: Rule) {
  await apiClient.request({ method: 'DELETE', path: `/api/v2/notification-rules/${encodeURIComponent(item.id)}` });
  await load();
}
async function savePublisher() {
  await apiClient.request({ method: 'POST', path: '/api/v2/notification-publishers', body: publisherBody(publisher, publisher.token ? { token: publisher.token } : {}) });
  publisher.token = ''; publisher.endpoint_url = '';
  await load();
}
async function togglePublisher(item: Publisher) {
  await apiClient.request({ method: 'PUT', path: `/api/v2/notification-publishers/${encodeURIComponent(item.id)}`, body: publisherBody({ ...item, enabled: !item.enabled }) });
  await load();
}
async function deletePublisher(item: Publisher) {
  await apiClient.request({ method: 'DELETE', path: `/api/v2/notification-publishers/${encodeURIComponent(item.id)}` });
  await load();
}

onMounted(() => { void load(); });
</script>

<template>
  <main class="v2-notification-settings">
    <header><p class="v2-eyebrow">通知与图床</p><h1>通知渠道</h1><p>秘密字段保存后只显示“已配置”，不会回显明文。图片发布失败时自动使用文本回退。</p></header>
    <p v-if="state.error" class="v2-error" role="alert">{{ state.error }}</p>

    <section>
      <h2>新增渠道</h2>
      <form @submit.prevent="saveChannel">
        <label>类型<select v-model="channel.channel_key"><option value="bark">Bark</option><option value="telegram">Telegram</option><option value="wecom">企业微信</option><option value="dingtalk">DingTalk</option><option value="feishu">Feishu</option><option value="generic_webhook">Webhook</option></select></label>
        <label>名称<input v-model.trim="channel.display_name" required /></label>
        <label v-if="endpointRequired">公开 HTTPS 端点<input v-model.trim="channel.endpoint_url" type="url" required /></label>
        <label v-if="channel.channel_key === 'telegram'">Chat ID<input v-model.trim="channel.chat_id" /></label>
        <label>秘密令牌<input v-model="channel.token" type="password" autocomplete="new-password" placeholder="保存后不回显" /></label>
        <label><input v-model="channel.use_proxy" type="checkbox" />复用账号通知代理</label>
        <button type="submit">保存渠道</button>
      </form>
      <article v-for="item in state.channels" :key="item.id">
        <div><strong>{{ item.display_name }}</strong><span>{{ item.channel_key }} · {{ configuredSecret(item.secret_configured) || '无秘密' }}</span></div>
        <div><button type="button" @click="toggleChannel(item)">{{ item.enabled ? '停用' : '启用' }}</button><button type="button" @click="testChannel(item)">发送测试</button><button type="button" @click="deleteChannel(item)">删除</button></div>
      </article>
      <p v-if="state.testResult" role="status">{{ state.testResult }}</p>
    </section>

    <section>
      <h2>通知规则</h2>
      <form @submit.prevent="saveRule">
        <label>事件<select v-model="rule.event_type"><option value="mail.new">新邮件</option><option value="send.failed">发送失败</option><option value="backup.completed">备份完成</option><option value="sync.failed">同步失败</option></select></label>
        <label>渠道<select v-model="rule.channel_id" required><option v-for="item in state.channels" :key="item.id" :value="item.id">{{ item.display_name }}</option></select></label>
        <label>图片发布器<select v-model="rule.image_publisher_id"><option value="">不使用</option><option v-for="item in state.publishers" :key="item.id" :value="item.id">{{ item.display_name }}</option></select></label>
        <label><input v-model="rule.use_proxy" type="checkbox" />允许代理</label>
        <button type="submit">创建规则</button>
      </form>
      <article v-for="item in state.rules" :key="item.id"><span>{{ item.event_type }} · {{ item.enabled ? '启用' : '停用' }}</span><div><button type="button" @click="toggleRule(item)">{{ item.enabled ? '停用' : '启用' }}</button><button type="button" @click="deleteRule(item)">删除</button></div></article>
    </section>

    <section>
      <h2>flymail-imgbed 图床</h2>
      <p>可选择维护的 flymail-imgbed 合同或经过审核的通用 HTTPS 发布器；发布失败只降级为文本通知。</p>
      <form @submit.prevent="savePublisher">
        <label>类型<select v-model="publisher.publisher_key"><option value="flymail_imgbed">flymail-imgbed</option><option value="generic_https">通用 HTTPS</option></select></label>
        <label>名称<input v-model.trim="publisher.display_name" required /></label>
        <label>端点<input v-model.trim="publisher.endpoint_url" type="url" required /></label>
        <label>秘密令牌<input v-model="publisher.token" type="password" autocomplete="new-password" /></label>
        <button type="submit">保存图床</button>
      </form>
      <article v-for="item in state.publishers" :key="item.id"><span>{{ item.display_name }} · {{ configuredSecret(item.secret_configured) || '无秘密' }}</span><div><button type="button" @click="togglePublisher(item)">{{ item.enabled ? '停用' : '启用' }}</button><button type="button" @click="deletePublisher(item)">删除</button></div></article>
    </section>
  </main>
</template>

<style scoped>
.v2-notification-settings{display:grid;gap:var(--v2-space-4);padding:var(--v2-space-4);max-width:1120px}
section{display:grid;gap:var(--v2-space-3);padding:var(--v2-space-4);border:1px solid var(--v2-border);border-radius:var(--v2-radius-md)}
form{display:flex;gap:var(--v2-space-3);align-items:end;flex-wrap:wrap}label{display:grid;gap:var(--v2-space-1)}article{display:flex;justify-content:space-between;gap:var(--v2-space-3);padding-block:var(--v2-space-2);border-top:1px solid var(--v2-border)}article>div{display:flex;gap:var(--v2-space-2);align-items:center}
</style>

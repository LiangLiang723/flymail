<script setup lang="ts">
import { onMounted, reactive } from 'vue';

import { apiClient } from '../../shared/api/client.ts';
import { normalizeApiError } from '../../shared/api/errors.ts';

interface Contact { id: string; display_name: string; primary_email: string; emails: string[] }
const state = reactive<{ items: Contact[]; error: string; editingId: string }>({ items: [], error: '', editingId: '' });
const form = reactive({ display_name: '', primary_email: '', emails: '' });

async function load() {
  const response = await apiClient.request<{ items: Contact[] }>({ method: 'GET', path: '/api/v2/contacts' });
  state.items = response.items;
}
function resetForm() {
  state.editingId = '';
  form.display_name = '';
  form.primary_email = '';
  form.emails = '';
}
function edit(item: Contact) {
  state.editingId = item.id;
  form.display_name = item.display_name;
  form.primary_email = item.primary_email;
  form.emails = item.emails.join(', ');
}
function payload() {
  return {
    display_name: form.display_name,
    primary_email: form.primary_email,
    emails: form.emails.split(',').map((item) => item.trim()).filter(Boolean),
  };
}
async function save() {
  state.error = '';
  try {
    if (state.editingId) {
      await apiClient.request({ method: 'PATCH', path: `/api/v2/contacts/${encodeURIComponent(state.editingId)}`, body: payload() });
    } else {
      await apiClient.request({ method: 'POST', path: '/api/v2/contacts', body: payload() });
    }
    resetForm();
    await load();
  } catch (value: unknown) { state.error = normalizeApiError(value).message; }
}
async function remove(item: Contact) {
  if (!window.confirm(`删除联系人“${item.display_name || item.primary_email}”？`)) return;
  await apiClient.request({ method: 'DELETE', path: `/api/v2/contacts/${encodeURIComponent(item.id)}` });
  if (state.editingId === item.id) resetForm();
  await load();
}

onMounted(() => { void load().catch((value) => { state.error = normalizeApiError(value).message; }); });
</script>

<template>
  <main class="v2-contacts-page">
    <header><p class="v2-eyebrow">通讯录</p><h1>联系人</h1></header>
    <form @submit.prevent="save">
      <input v-model.trim="form.display_name" aria-label="姓名" placeholder="姓名" />
      <input v-model.trim="form.primary_email" aria-label="主邮箱" type="email" placeholder="主邮箱" required />
      <input v-model="form.emails" aria-label="其他邮箱" placeholder="其他邮箱，逗号分隔" />
      <button type="submit">{{ state.editingId ? '保存修改' : '添加联系人' }}</button>
      <button v-if="state.editingId" type="button" @click="resetForm">取消编辑</button>
    </form>
    <p v-if="state.error" class="v2-error" role="alert">{{ state.error }}</p>
    <ul>
      <li v-for="item in state.items" :key="item.id">
        <div><strong>{{ item.display_name || item.primary_email }}</strong><span>{{ item.primary_email }}</span></div>
        <div><button type="button" @click="edit(item)">编辑</button><button type="button" @click="remove(item)">删除</button></div>
      </li>
    </ul>
    <p v-if="!state.items.length">还没有联系人。添加后，写信地址自动完成会优先显示这里的结果。</p>
  </main>
</template>

<style scoped>
.v2-contacts-page{display:grid;gap:var(--v2-space-4);padding:var(--v2-space-4)}form,li,li>div{display:flex;gap:var(--v2-space-2);align-items:center;flex-wrap:wrap}ul{display:grid;gap:var(--v2-space-2);padding:0;list-style:none}li{justify-content:space-between;padding:var(--v2-space-3);border:1px solid var(--v2-border);border-radius:var(--v2-radius-md)}li>div:first-child{display:grid}
</style>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { apiClient } from '../../shared/api/client.ts';
import { normalizeApiError } from '../../shared/api/errors.ts';
import AdvancedFilters from './AdvancedFilters.vue';
import SearchBar from './SearchBar.vue';
import SearchResults from './SearchResults.vue';
import {
  SearchController,
  appendSearchResults,
  deserializeSearchFilters,
  serializeSearchFilters,
  validateSearchFilters,
  type SearchFilters,
  type SearchResponse,
  type SearchResultItem,
} from './search-state.ts';

interface Suggestion { kind: string; value: string; label: string }
interface SavedSearch { id: string; name: string; filters: SearchFilters; is_pinned: boolean }
interface HistoryItem { sequence_id: number; filters: SearchFilters; created_at: number }

const route = useRoute();
const router = useRouter();
const filters = ref<SearchFilters>(deserializeSearchFilters(route.query));
const keyword = ref(String(filters.value.keyword || ''));
const suggestions = ref<Suggestion[]>([]);
const showFilters = ref(false);
const saveName = ref('');
const state = reactive<{ items: SearchResultItem[]; nextCursor: string | null; loading: boolean; error?: string; parser: string }>({ items: [], nextCursor: null, loading: false, parser: '' });
const savedSearches = ref<SavedSearch[]>([]);
const history = ref<HistoryItem[]>([]);
const searchCache = new Map<string, SearchResponse>();

const controller = new SearchController((current, signal, cursor) => apiClient.request<SearchResponse>({
  method: 'POST', path: '/api/v2/search', signal, body: { filters: current, limit: 20, cursor: cursor || null },
}));

function cacheKey(current: SearchFilters, cursor?: string | null) {
  return JSON.stringify([validateSearchFilters(current), cursor || null]);
}

async function runSearch(cursor?: string | null) {
  const current = validateSearchFilters({ ...filters.value, keyword: keyword.value });
  if (!Object.keys(current).length) {
    await router.replace('/mail/semantic/inbox');
    return;
  }
  filters.value = current;
  await router.replace({ name: 'search', query: serializeSearchFilters(current) });
  state.loading = true;
  state.error = undefined;
  const key = cacheKey(current, cursor);
  const cached = searchCache.get(key);
  if (cached) {
    state.items = cursor ? appendSearchResults(state.items, cached.items) : cached.items;
    state.nextCursor = cached.next_cursor;
    state.parser = cached.fulltext_parser;
    state.loading = false;
  }
  try {
    const response = await controller.search(current, cursor);
    searchCache.set(key, response);
    state.items = cursor ? appendSearchResults(state.items, response.items) : response.items;
    state.nextCursor = response.next_cursor;
    state.parser = response.fulltext_parser;
    await loadHistory();
  } catch (value: unknown) {
    if (value instanceof DOMException && value.name === 'AbortError') return;
    state.error = normalizeApiError(value).message;
  } finally {
    state.loading = false;
  }
}

let suggestionController: AbortController | undefined;
async function loadSuggestions(value: string) {
  suggestionController?.abort();
  if (!value.trim()) { suggestions.value = []; return; }
  suggestionController = new AbortController();
  try {
    const response = await apiClient.request<{ items: Suggestion[] }>({
      method: 'GET', path: '/api/v2/search/suggestions', query: { q: value }, signal: suggestionController.signal,
    });
    suggestions.value = response.items;
  } catch (value: unknown) {
    if (!(value instanceof DOMException && value.name === 'AbortError')) suggestions.value = [];
  }
}

async function loadHistory() {
  const response = await apiClient.request<{ items: HistoryItem[] }>({ method: 'GET', path: '/api/v2/search/history' });
  history.value = response.items;
}
async function clearHistory() {
  await apiClient.request({ method: 'DELETE', path: '/api/v2/search/history' });
  history.value = [];
}
async function loadSaved() {
  const response = await apiClient.request<{ items: SavedSearch[] }>({ method: 'GET', path: '/api/v2/saved-searches' });
  savedSearches.value = response.items.map((item) => ({ ...item, filters: validateSearchFilters(item.filters) }));
}
async function saveCurrent() {
  if (!saveName.value.trim()) return;
  const saved = await apiClient.request<SavedSearch>({
    method: 'POST', path: '/api/v2/saved-searches', body: { name: saveName.value.trim(), filters: validateSearchFilters({ ...filters.value, keyword: keyword.value }), is_pinned: true },
  });
  savedSearches.value = [...savedSearches.value, saved];
  saveName.value = '';
}
function restoreSaved(saved: SavedSearch) {
  const restored = validateSearchFilters(saved.filters);
  filters.value = restored;
  keyword.value = String(restored.keyword || '');
  void runSearch();
}
function applyFilters(value: SearchFilters) {
  filters.value = value;
  showFilters.value = false;
  void runSearch();
}
function openThread(threadId: string) {
  void router.push({ name: 'mail', params: { scope: 'semantic', key: 'inbox' }, query: { thread: threadId, fromSearch: '1' } });
}

watch(keyword, (value) => { void loadSuggestions(value); });
onMounted(async () => {
  await Promise.all([loadHistory(), loadSaved()]);
  if (Object.keys(filters.value).length) await runSearch();
});
onBeforeUnmount(() => { controller.cancel(); suggestionController?.abort(); });
</script>

<template>
  <main class="v2-search-page">
    <header><div><p class="v2-eyebrow">本地搜索</p><h1>搜索邮件</h1></div><button type="button" @click="showFilters = true">高级条件</button></header>
    <aside class="v2-search-boundary" role="note">搜索只使用本地元数据、索引和已缓存正文；未缓存或已释放的正文不会被正文关键词命中。</aside>
    <SearchBar v-model="keyword" :suggestions="suggestions" @submit="runSearch()" @suggestion="keyword = $event; runSearch()" />
    <div class="v2-search-save"><input v-model="saveName" placeholder="保存搜索名称" /><button type="button" @click="saveCurrent">保存搜索</button></div>
    <section v-if="savedSearches.length"><h2>保存的搜索</h2><button v-for="saved in savedSearches" :key="saved.id" type="button" @click="restoreSaved(saved)">{{ saved.name }}</button></section>
    <section><h2>搜索历史</h2><button type="button" @click="clearHistory">清除历史</button><button v-for="item in history" :key="item.sequence_id" type="button" @click="filters = validateSearchFilters(item.filters); keyword = String(filters.keyword || ''); runSearch()">{{ item.filters.keyword || '高级条件' }}</button></section>
    <p v-if="state.error" class="v2-error" role="alert">{{ state.error }}</p>
    <SearchResults :items="state.items" :loading="state.loading" :next-cursor="state.nextCursor" @open="openThread" @load-more="runSearch(state.nextCursor)" />
    <AdvancedFilters :open="showFilters" :model-value="filters" @apply="applyFilters" @cancel="showFilters = false" @close="showFilters = false" />
  </main>
</template>

<style scoped>
.v2-search-page { min-height: 100%; padding: var(--v2-space-4); background: var(--v2-surface); }
.v2-search-page > header { display: flex; justify-content: space-between; align-items: center; gap: var(--v2-space-3); }
.v2-search-page h1, .v2-search-page .v2-eyebrow { margin: 0; }
.v2-search-boundary { margin-block: var(--v2-space-3); padding: var(--v2-space-3); border-radius: var(--v2-radius-sm); background: var(--v2-surface-muted); }
.v2-search-save { display: flex; gap: var(--v2-space-2); margin-block: var(--v2-space-3); }
</style>

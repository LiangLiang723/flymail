import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import api from '../utils/api';
import type {
  SignatureDraft,
  SignatureEntrySource,
  SignatureTemplate,
} from '../types/signature';
import {
  createEmptySignatureDraft,
  createSignatureDraft,
  duplicateSignatureDraft,
  filterSignatures,
  serializeSignatureDraft,
} from '../utils/signature-management';
import { useUIStore } from './ui';

function normalizeSignature(value: any): SignatureTemplate {
  return {
    id: Number(value?.id || 0),
    name: String(value?.name || ''),
    content_html: String(value?.content_html || ''),
    account_id: String(value?.account_id || ''),
    is_default: Boolean(value?.is_default),
    is_reply_default: Boolean(value?.is_reply_default),
  };
}

function getErrorMessage(error: any): string {
  return String(error?.detail || error?.message || '操作失败');
}

export const useSignatureStore = defineStore('signatures', () => {
  const uiStore = useUIStore();
  const signatures = ref<SignatureTemplate[]>([]);
  const loaded = ref(false);
  const loading = ref(false);
  const saving = ref(false);
  const deleting = ref(false);
  const search = ref('');
  const accountFilter = ref('all');
  const selectedId = ref<number | null>(null);
  const draft = ref<SignatureDraft>(createEmptySignatureDraft());
  const savedDraftSnapshot = ref(serializeSignatureDraft(draft.value));
  const entrySource = ref<SignatureEntrySource>('menu');
  const mobileEditing = ref(false);

  const filteredSignatures = computed(() => filterSignatures(
    signatures.value,
    search.value,
    accountFilter.value,
  ));
  const selectedSignature = computed(() => (
    selectedId.value === null
      ? null
      : signatures.value.find((signature) => signature.id === selectedId.value) || null
  ));
  const hasUnsavedChanges = computed(() => (
    serializeSignatureDraft(draft.value) !== savedDraftSnapshot.value
  ));
  const signatureCount = computed(() => signatures.value.length);

  function setDraft(nextDraft: SignatureDraft) {
    draft.value = nextDraft;
    savedDraftSnapshot.value = serializeSignatureDraft(nextDraft);
  }

  function syncDraftFromSelection() {
    const selected = selectedSignature.value;
    if (selected) {
      setDraft(createSignatureDraft(selected));
      return;
    }
    setDraft(createEmptySignatureDraft());
  }

  async function loadSignatures() {
    if (loading.value) return;
    loading.value = true;
    const preserveDraft = hasUnsavedChanges.value;
    try {
      const data = await api.get('/signatures') as any;
      signatures.value = Array.isArray(data?.signatures)
        ? data.signatures.map(normalizeSignature).filter((signature: SignatureTemplate) => signature.id > 0)
        : [];
      loaded.value = true;

      if (selectedId.value !== null && !signatures.value.some((item) => item.id === selectedId.value)) {
        selectedId.value = null;
      }
      if (selectedId.value === null && signatures.value.length > 0 && !preserveDraft) {
        selectedId.value = signatures.value[0].id;
      }
      if (!preserveDraft) syncDraftFromSelection();
    } finally {
      loading.value = false;
    }
  }

  async function ensureLoaded() {
    if (!loaded.value) await loadSignatures();
  }

  function beginCreate(accountId = '') {
    selectedId.value = null;
    setDraft(createEmptySignatureDraft(accountId));
    mobileEditing.value = true;
  }

  function beginEdit(id: number) {
    const signature = signatures.value.find((item) => item.id === id);
    if (!signature) return false;
    selectedId.value = id;
    setDraft(createSignatureDraft(signature));
    mobileEditing.value = true;
    return true;
  }

  function beginDuplicate(id: number) {
    const signature = signatures.value.find((item) => item.id === id);
    if (!signature) return false;
    selectedId.value = null;
    const duplicate = duplicateSignatureDraft(signature);
    duplicate.is_default = false;
    duplicate.is_reply_default = false;
    setDraft(duplicate);
    mobileEditing.value = true;
    return true;
  }

  async function saveDraft() {
    const name = draft.value.name.trim();
    if (!name) throw new Error('请输入签名名称');

    const scope = draft.value.account_id;
    const oldNewDefaultId = signatures.value.find((item) => (
      item.account_id === scope && item.is_default && item.id !== draft.value.id
    ))?.id;
    const oldReplyDefaultId = signatures.value.find((item) => (
      item.account_id === scope && item.is_reply_default && item.id !== draft.value.id
    ))?.id;
    const payload = {
      name,
      content_html: draft.value.content_html,
      account_id: scope,
      is_default: draft.value.is_default,
      is_reply_default: draft.value.is_reply_default,
    };

    saving.value = true;
    try {
      let savedId = draft.value.id;
      if (draft.value.id !== null) {
        await api.put(`/signatures/${draft.value.id}`, payload);
      } else {
        const created = await api.post('/signatures', payload) as any;
        savedId = Number(created?.id || 0) || null;
      }

      selectedId.value = savedId;
      savedDraftSnapshot.value = serializeSignatureDraft({ ...draft.value, id: savedId, name });
      await loadSignatures();
      if (savedId !== null) beginEdit(savedId);
      mobileEditing.value = true;

      const replacedNewDefault = draft.value.is_default && oldNewDefaultId !== undefined;
      const replacedReplyDefault = draft.value.is_reply_default && oldReplyDefaultId !== undefined;
      if (replacedNewDefault || replacedReplyDefault) {
        uiStore.info('已替换该范围原有默认签名');
      }
      uiStore.success('签名已保存');
      return savedId;
    } catch (error) {
      throw new Error(getErrorMessage(error));
    } finally {
      saving.value = false;
    }
  }

  async function deleteSelected() {
    if (selectedId.value === null) return false;
    deleting.value = true;
    try {
      await api.delete(`/signatures/${selectedId.value}`);
      const deletedId = selectedId.value;
      signatures.value = signatures.value.filter((item) => item.id !== deletedId);
      selectedId.value = signatures.value[0]?.id ?? null;
      syncDraftFromSelection();
      mobileEditing.value = false;
      uiStore.success('签名已删除');
      return true;
    } catch (error) {
      throw new Error(getErrorMessage(error));
    } finally {
      deleting.value = false;
    }
  }

  function discardDraft() {
    syncDraftFromSelection();
  }

  function setEntrySource(source: SignatureEntrySource) {
    entrySource.value = source;
  }

  function resetWorkspace() {
    search.value = '';
    accountFilter.value = 'all';
    mobileEditing.value = false;
    entrySource.value = 'menu';
    if (selectedId.value === null && signatures.value.length > 0) {
      selectedId.value = signatures.value[0].id;
    }
    syncDraftFromSelection();
  }

  return {
    signatures,
    loaded,
    loading,
    saving,
    deleting,
    search,
    accountFilter,
    selectedId,
    draft,
    savedDraftSnapshot,
    entrySource,
    mobileEditing,
    filteredSignatures,
    selectedSignature,
    hasUnsavedChanges,
    signatureCount,
    loadSignatures,
    ensureLoaded,
    beginCreate,
    beginEdit,
    beginDuplicate,
    saveDraft,
    deleteSelected,
    discardDraft,
    setEntrySource,
    resetWorkspace,
  };
});

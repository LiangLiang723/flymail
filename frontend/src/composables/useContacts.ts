/** 联系人管理 composable
 *
 * 封装联系人列表、写信自动补全、往来统计，以及从本地邮件候选中批量导入联系人。
 */
import { ref } from 'vue';
import api from '../utils/api';

export interface ContactEmail {
  id: number;
  email: string;
  is_primary: boolean;
}

export interface ContactItem {
  id: number;
  name: string;
  emails: ContactEmail[];
  phone: string;
  company: string;
  remark: string;
  group_name: string;
}

export interface ContactSuggestion {
  contact_id: number;
  name: string;
  email: string;
}

export interface ContactStats {
  count: number;
  last_date: string;
}

export interface ContactCandidate {
  name: string;
  email: string;
  sent_count: number;
  received_count: number;
  total_count: number;
  last_date: string;
}

export interface ContactImportResult {
  imported: number;
  skipped: number;
}

export function formatContactTag(item: { name: string; email: string }): string {
  return item.name ? `${item.name} <${item.email}>` : item.email;
}

export function useContacts() {
  const contacts = ref<ContactItem[]>([]);
  const loading = ref(false);

  async function loadContacts(search = '') {
    loading.value = true;
    try {
      const data = await api.get('/contacts', { params: search ? { search } : {} }) as any;
      contacts.value = data.contacts || [];
    } finally {
      loading.value = false;
    }
  }

  async function addContact(data: {
    name: string;
    emails: string[];
    phone?: string;
    company?: string;
    remark?: string;
    group_name?: string;
  }): Promise<boolean> {
    await api.post('/contacts', data);
    return true;
  }

  async function editContact(id: number, data: {
    name: string;
    emails: string[];
    phone?: string;
    company?: string;
    remark?: string;
    group_name?: string;
  }): Promise<boolean> {
    await api.put(`/contacts/${id}`, { ...data, id });
    return true;
  }

  async function removeContact(id: number): Promise<boolean> {
    await api.delete(`/contacts/${id}`);
    return true;
  }

  async function searchContacts(q: string): Promise<ContactSuggestion[]> {
    if (!q || q.length < 1) return [];
    const data = await api.get('/contacts/search', { params: { q } }) as any;
    const results: ContactSuggestion[] = [];
    for (const contact of (data.results || [])) {
      for (const emailObj of (contact.emails || [])) {
        results.push({
          contact_id: contact.id,
          name: contact.name,
          email: emailObj.email,
        });
      }
    }
    return results.slice(0, 10);
  }

  async function quickAddContact(name: string, email: string): Promise<ContactItem> {
    return await api.post('/contacts/quick-add', { name, email }) as ContactItem;
  }

  async function getContactStats(contactId: number, email: string): Promise<ContactStats> {
    return await api.get(`/contacts/${contactId}/stats`, { params: { email } }) as ContactStats;
  }

  async function loadContactCandidates(accountId: string, search = ''): Promise<ContactCandidate[]> {
    if (!accountId) return [];
    const params: Record<string, string> = { account_id: accountId };
    if (search.trim()) params.search = search.trim();
    const data = await api.get('/contacts/candidates', { params }) as any;
    return data.candidates || [];
  }

  async function importContactCandidates(
    accountId: string,
    selectedContacts: Array<{ name: string; email: string }>,
  ): Promise<ContactImportResult> {
    return await api.post('/contacts/import', {
      account_id: accountId,
      contacts: selectedContacts,
    }) as ContactImportResult;
  }

  return {
    contacts,
    loading,
    loadContacts,
    addContact,
    editContact,
    removeContact,
    searchContacts,
    quickAddContact,
    getContactStats,
    loadContactCandidates,
    importContactCandidates,
  };
}

import type { RouteLocationRaw } from 'vue-router';

import type {
  NavigationAccount,
  NavigationAccountModel,
  NavigationMailbox,
  SavedSearchNavigationItem,
} from '../../entities/account/types.ts';
import type { AccountSummary } from '../../shared/api/generated.ts';

export type NavigationTarget =
  | { kind: 'semantic'; key: string }
  | { kind: 'account'; accountId: string; key: string }
  | { kind: 'native'; accountId: string; key: string }
  | { kind: 'saved'; id: string };

export interface NavigationModel {
  semantic: NavigationMailbox[];
  accounts: NavigationAccountModel[];
  savedSearches: SavedSearchNavigationItem[];
}

const SEMANTIC_ORDER = ['inbox', 'sent', 'drafts', 'archive', 'junk', 'trash'] as const;
const SEMANTIC_NAMES: Record<string, string> = {
  inbox: '收件箱', sent: '已发送', drafts: '草稿', archive: '归档', junk: '垃圾邮件', trash: '已删除',
};

export function toNavigationAccounts(accounts: AccountSummary[]): NavigationAccount[] {
  return accounts.map((account) => ({
    id: account.id,
    providerKey: account.provider_key,
    displayName: account.display_name,
    email: account.email,
    status: account.status,
    iconUrl: account.icon_url,
    semanticMailboxes: (account.semantic_mailboxes || []).map((mailbox) => ({
      key: mailbox.key,
      name: mailbox.name,
      unreadCount: Number(mailbox.unread_count || 0),
    })),
    nativeLabels: (account.native_labels || []).map((label) => ({
      key: label.key,
      name: label.name,
      unreadCount: Number(label.unread_count || 0),
    })),
  }));
}

export function buildNavigationModel(
  accounts: NavigationAccount[],
  savedSearches: SavedSearchNavigationItem[],
): NavigationModel {
  const semanticTotals = new Map<string, number>();
  for (const account of accounts) {
    for (const mailbox of account.semanticMailboxes) {
      semanticTotals.set(mailbox.key, (semanticTotals.get(mailbox.key) || 0) + mailbox.unreadCount);
    }
  }
  const semantic = SEMANTIC_ORDER.map((key) => ({
    key,
    name: SEMANTIC_NAMES[key],
    unreadCount: semanticTotals.get(key) || 0,
  }));
  const accountModels: NavigationAccountModel[] = accounts.map((account) => ({
    ...account,
    action: account.status === 'auth_required'
      ? 'reauthorize'
      : account.status === 'disabled'
        ? 'enable'
        : account.status === 'pending_verification'
          ? 'verify'
          : undefined,
  }));
  return { semantic, accounts: accountModels, savedSearches: [...savedSearches] };
}

export function navigationLocation(target: NavigationTarget): RouteLocationRaw {
  if (target.kind === 'semantic') {
    return { name: 'mail', params: { scope: 'semantic', key: target.key } };
  }
  if (target.kind === 'account') {
    return { name: 'mail', params: { scope: 'account', key: target.accountId }, query: { mailbox: target.key } };
  }
  if (target.kind === 'native') {
    return { name: 'mail', params: { scope: 'native', key: target.accountId }, query: { label: target.key } };
  }
  return { name: 'search', query: { saved: target.id } };
}

export function patchNavigationBadge(
  model: NavigationModel,
  patch: { accountId: string; key: string; unreadCount: number },
): NavigationModel {
  let changed = false;
  const accounts = model.accounts.map((account) => {
    if (account.id !== patch.accountId) return account;
    let labelsChanged = false;
    const nativeLabels = account.nativeLabels.map((label) => {
      if (label.key !== patch.key || label.unreadCount === patch.unreadCount) return label;
      labelsChanged = true;
      return { ...label, unreadCount: Math.max(0, patch.unreadCount) };
    });
    if (!labelsChanged) return account;
    changed = true;
    return { ...account, nativeLabels };
  });
  return changed ? { ...model, accounts } : model;
}

export function createNavigationState(options: {
  expandedAccountIds?: string[];
  restoreFocus?: () => void;
} = {}) {
  const expanded = new Set(options.expandedAccountIds || []);
  let drawerOpen = false;
  let selected: NavigationTarget | undefined;
  return {
    get drawerOpen() { return drawerOpen; },
    get selected() { return selected; },
    isExpanded(accountId: string) { return expanded.has(accountId); },
    openDrawer() { drawerOpen = true; },
    closeDrawer() {
      if (!drawerOpen) return;
      drawerOpen = false;
      options.restoreFocus?.();
    },
    select(target: NavigationTarget) {
      selected = target;
      if (drawerOpen) {
        drawerOpen = false;
        options.restoreFocus?.();
      }
    },
    toggleAccount(accountId: string) {
      if (expanded.has(accountId)) expanded.delete(accountId);
      else expanded.add(accountId);
    },
    preference() {
      return { expanded_account_ids: [...expanded].sort() };
    },
  };
}

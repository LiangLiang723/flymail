export type NavigationAccountStatus = 'active' | 'disabled' | 'auth_required' | 'pending' | 'error';

export interface NavigationMailbox {
  key: string;
  name: string;
  unreadCount: number;
  semanticKey?: string;
}

export interface NavigationAccount {
  id: string;
  providerKey: string;
  displayName: string;
  email: string;
  status: NavigationAccountStatus;
  iconUrl?: string | null;
  semanticMailboxes: NavigationMailbox[];
  nativeLabels: NavigationMailbox[];
}

export interface NavigationAccountModel extends NavigationAccount {
  action?: 'reauthorize' | 'enable' | 'verify';
}

export interface SavedSearchNavigationItem {
  id: string;
  name: string;
}

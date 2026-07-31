export type AccountIconType = 'default' | 'preset' | 'upload';

export interface MailAccount {
  id: string;
  email: string;
  provider: string;
  status: string;
  remark: string;
  group_name: string;
  hide_email: boolean;
  sort_order: number;
  poll_interval_seconds: number;
  created_at: number;
  reauth_needed?: boolean;
  icon_type: AccountIconType;
  icon_value: string;
  icon_url: string;
}

export interface AccountIconFields {
  icon_type: AccountIconType;
  icon_value: string;
  icon_url: string;
}

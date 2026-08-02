// Curated TypeScript surface reviewed against the frozen FlyMail V2 OpenAPI contract.
// The backend fixture is a contract fingerprint summary rather than a complete OpenAPI document.
export const OPENAPI_VERSION = '0.0.25' as const;
export const OPENAPI_SHA256 = 'e156e46739ef5c19e1f22077e4958990854ea3ec4a6ce80c01e936474b84ba79' as const;

export type UserRole = 'user' | 'admin';
export type AccountStatus = 'active' | 'disabled' | 'auth_required' | 'pending' | 'error';
export type BodyState = 'not_requested' | 'queued' | 'fetching' | 'ready' | 'evicted' | 'failed' | 'unavailable';
export type OperationStatus = 'pending' | 'applying' | 'synced' | 'conflict' | 'failed' | 'cancelled' | 'review_required';

export interface UserSummary {
  id: string;
  username: string;
  role: UserRole;
  enabled: boolean;
  nickname?: string;
  avatar_object_sha256?: string | null;
  display_name?: string;
  avatar_url?: string | null;
}

export interface AccountSummary {
  id: string;
  provider_key: string;
  email: string;
  display_name: string;
  remark: string;
  group_name: string;
  status: AccountStatus;
  include_in_unified: boolean;
  runtime_status: string;
  idle_status: string;
  icon_mode: string;
  icon_value: string;
  icon_object_sha256: string | null;
  total_count: number;
  unread_count: number;
}

export interface BootstrapNavigationMailbox {
  id: string;
  semantic_key: string;
  native_key: string;
  native_name: string;
  total_count: number;
  unread_count: number;
  sync_status: string;
}

export interface BootstrapAccountNavigation {
  account_id: string;
  semantic_mailboxes: BootstrapNavigationMailbox[];
  native_labels: BootstrapNavigationMailbox[];
}

export interface BootstrapResponse {
  user: UserSummary;
  permissions: string[];
  accounts: AccountSummary[];
  navigation: {
    unified: { account_ids: string[]; total_count: number; unread_count: number };
    accounts: BootstrapAccountNavigation[];
  };
  ui_preferences: {
    theme: 'system' | 'light' | 'dark';
    density: 'comfortable' | 'compact';
    expanded_account_ids: string[];
  };
  sync_alert_summary: {
    auth_required_accounts: number;
    degraded_accounts: number;
    pending_accounts: number;
    unread_notifications: number;
  };
  csrf_token: string;
  realtime_cursor: number;
  version: string;
}

export interface AuthResponse {
  user: UserSummary;
  csrf_token: string;
}

export interface ThreadProjection {
  id: string;
  subject: string;
  snippet?: string;
  participants?: Array<{ name?: string; address: string }>;
  latest_at: number;
  unread_count: number;
  message_count: number;
  is_starred: boolean;
  has_attachments: boolean;
  account_ids: string[];
  mailbox_keys?: string[];
  native_labels?: string[];
  pending_state?: string | null;
  operation_status?: OperationStatus | null;
}

export interface CursorPage<T> {
  items: T[];
  next_cursor?: string | null;
}

export interface ThreadListResponse {
  threads: ThreadProjection[];
  next_cursor?: string | null;
}

export interface AttachmentSummary {
  id: string;
  filename: string;
  content_type?: string;
  size_bytes?: number;
  is_inline?: boolean;
  cache_state?: BodyState;
  download_url?: string | null;
}

export interface MessageSummary {
  id: string;
  account_id: string;
  from: Array<{ name?: string; address: string }>;
  to: Array<{ name?: string; address: string }>;
  cc?: Array<{ name?: string; address: string }>;
  subject: string;
  sent_at?: number;
  received_at?: number;
  is_read: boolean;
  is_starred: boolean;
  body_state: BodyState;
  body_version?: string;
  attachments: AttachmentSummary[];
}

export interface ThreadDetailResponse {
  id: string;
  subject: string;
  messages: MessageSummary[];
  projection: ThreadProjection;
}

export interface BodyResponse {
  message_id: string;
  state: BodyState;
  html?: string;
  text?: string;
  content_version?: string;
  task_id?: string;
  retryable?: boolean;
}

export interface OperationResponse {
  operation_id: string;
  status: OperationStatus;
  projection?: ThreadProjection;
  undo_token?: string | null;
  undo_expires_at?: number | null;
  partial_results?: Array<{ message_id: string; status: string; message?: string }>;
}

export interface DraftSummary {
  id: string;
  version: number;
  status: string;
  send_state: string;
  account_id: string;
  identity_id: string;
  subject: string;
  body_html?: string;
  body_text?: string;
  recipients?: Record<'to' | 'cc' | 'bcc', Array<{ name?: string; address: string }>>;
  attachments?: AttachmentSummary[];
  updated_at?: number;
}

export interface SettingsResponse {
  body_cache_quota_bytes: number;
  attachment_cache_quota_bytes: number;
  body_cache_usage_bytes: number;
  attachment_cache_usage_bytes: number;
  cleanup_task_id?: string | null;
  ui_preferences: Record<string, unknown>;
  compose_preferences: Record<string, unknown>;
  remote_image_policy: Record<string, unknown>;
  updated_at: number;
}

export interface SyncAccountStatus {
  account_id: string;
  status: string;
  idle_status?: string;
  next_reconcile_at?: number;
  pending_operations: number;
  conflicts: number;
  phases: Record<string, unknown> | string[];
  safe_error?: string | null;
}

export interface SyncCenterResponse {
  accounts: SyncAccountStatus[];
}

export interface ContactSummary {
  id: string;
  display_name: string;
  primary_email: string;
  emails: string[];
  updated_at?: number;
}

export interface NotificationSummary {
  id: string;
  event_type: string;
  title: string;
  summary?: string;
  action_path?: string | null;
  read_at?: number | null;
  dismissed_at?: number | null;
  created_at: number;
}

export interface RealtimeEvent {
  sequence: number;
  event_type: string;
  aggregate_id?: string | null;
  occurred_at: number;
  payload: Record<string, unknown>;
}

export interface BackupSummary {
  id: string;
  status: string;
  created_at: number;
  size_bytes?: number;
  manifest_version?: number;
  counts?: Record<string, number>;
}

// Curated TypeScript surface reviewed against the frozen FlyMail V2 OpenAPI contract.
// The backend fixture is a contract fingerprint summary rather than a complete OpenAPI document.
export const OPENAPI_VERSION = '0.0.25' as const;
export const OPENAPI_SHA256 = '1552538e3c7cd1062d1ce51b9c6f99b8829fef0b6abc9c4a9add88c33971c6ac' as const;

export type UserRole = 'user' | 'admin';
export type AccountStatus = 'active' | 'disabled' | 'auth_required' | 'pending_verification' | 'error';
export type BodyState = 'not_requested' | 'queued' | 'fetching' | 'ready' | 'evicted' | 'failed' | 'unavailable';
export type OperationStatus = 'pending' | 'applying' | 'synced' | 'conflict' | 'failed' | 'cancelled' | 'review_required';

export interface UserSummary {
  id: string;
  username: string;
  role: UserRole;
  enabled: boolean;
  display_name?: string;
  avatar_url?: string | null;
}

export interface AccountSummary {
  id: string;
  provider_key: string;
  email: string;
  display_name: string;
  status: AccountStatus;
  icon_url?: string | null;
  unread_count?: number;
  pending_operations?: number;
  semantic_mailboxes?: Array<{ key: string; name: string; unread_count?: number }>;
  native_labels?: Array<{ key: string; name: string; unread_count?: number }>;
}

export interface BootstrapResponse {
  user: UserSummary;
  permissions: string[];
  accounts: AccountSummary[];
  preferences: {
    theme?: 'system' | 'light' | 'dark';
    density?: 'comfortable' | 'compact';
    [key: string]: unknown;
  };
  navigation?: Record<string, unknown>;
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

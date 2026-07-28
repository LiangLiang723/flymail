/** 邮件相关类型定义 */

/** 附件 */
export interface Attachment {
  filename: string
  content_type: string
  size: number
  part_number: number
  content_id: string
  is_inline: boolean
  local_path?: string
}

/** 邮件消息 */
export interface Message {
  id: string
  uid?: number
  from_addr: string
  to_addr?: string
  cc?: string
  reply_to?: string
  subject: string
  date: string
  is_read: boolean
  body_text?: string
  body_html?: string
  attachments?: Attachment[]
  has_attachments?: boolean
  message_id?: string
  account_id?: string
  account_email?: string
  account_provider?: string
  folder?: string
}

export interface BackupAccount {
  id: string
  email: string
  provider: string
  selected: boolean
}

export interface BackupDir {
  path: string
  label: string
  writable: boolean
  exists: boolean
}

export interface BackupSettings {
  enabled: boolean
  account_ids: string[]
  target_dir: string
  available_dirs: BackupDir[]
  current_root: string
  accounts: BackupAccount[]
}

export interface BackupAccountStatus {
  account_id: string
  count: number
  deleted_count: number
  last_archived: number
  email: string
  provider: string
}

export interface BackupFolder {
  folder: string
  count: number
  deleted_count: number
}

export interface BackupStatus {
  total: number
  deleted: number
  last_archived: number
  accounts: BackupAccountStatus[]
}

export interface ArchivedMessage {
  id: number
  user_uid?: string
  account_id: string
  folder: string
  uid: number
  message_id?: string
  subject: string
  from_addr: string
  to_addr?: string
  cc?: string
  date: string
  size?: number
  eml_path?: string
  flags?: string
  has_attachments: number
  archived_at: number
  is_deleted_on_server: number
  deleted_at?: number
}

export interface BackupAttachment {
  filename: string
  content_type: string
  size: number
  part_number: number
  is_inline: boolean
}

export interface ArchivedMessageDetail {
  id: string
  uid: number
  subject: string
  from_addr: string
  to_addr?: string
  cc?: string
  reply_to?: string
  date: string
  body_text?: string
  body_html?: string
  attachments: BackupAttachment[]
  has_attachments: boolean
  is_deleted_on_server: number
  archived_at: number
  size: number
}

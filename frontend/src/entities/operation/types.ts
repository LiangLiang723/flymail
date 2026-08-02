import type { OperationStatus, ThreadProjection } from '../../shared/api/generated.ts';

export interface OperationCommand {
  target_type: 'thread' | 'remote_instance';
  target_id: string;
  operation_type: string;
  desired_state: Record<string, unknown>;
  confirmation_token?: string;
}

export interface OperationAccepted {
  operation_group_id: string;
  operation_ids: string[];
  projection?: ThreadProjection;
  undo_token?: string | null;
  undo_expires_at?: number | null;
  partial_results?: Array<{ message_id: string; status: string; message?: string }>;
}

export interface PendingOperation {
  operationId: string;
  status: OperationStatus;
  partialResults: Array<{ message_id: string; status: string; message?: string }>;
}

export type ConflictKind = 'draft_version' | 'uncertain_send' | 'missing_mailbox' | 'operation_conflict' | string;

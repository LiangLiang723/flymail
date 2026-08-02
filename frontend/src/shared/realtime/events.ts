import type { RealtimeEvent } from '../api/generated.ts';

export const KNOWN_REALTIME_EVENTS = new Set([
  'account.updated',
  'sync.state',
  'thread.updated',
  'thread.removed',
  'message.body_state',
  'attachment.cache_state',
  'draft.updated',
  'send.state',
  'operation.state',
  'conflict.created',
  'conflict.resolved',
  'cache.cleanup_state',
  'notification.created',
  'notification.read',
  'backup.state',
  'restore.validation_state',
  'admin.user_state',
  'auth.session_revoked',
  'system.version_changed',
]);

export interface RealtimeProjectionHandlers {
  onEvent?: (event: RealtimeEvent) => void;
  patchThread?: (threadId: string, projection?: Record<string, unknown>) => void;
  removeThread?: (threadId: string) => void;
  invalidateThread?: (threadId: string) => void;
  invalidateBody?: (messageId: string) => void;
  invalidateScopes?: (scopes: string[]) => void;
  authExpired?: () => void;
  versionChanged?: (version: string) => void;
  statusFallback?: () => void;
}

function record(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

export function decodeRealtimeEvent(value: unknown): RealtimeEvent | undefined {
  const raw = record(value);
  if (!raw) return undefined;
  const sequence = Number(raw.sequence);
  const eventType = typeof raw.event_type === 'string' ? raw.event_type : '';
  const occurredAt = Number(raw.occurred_at);
  const payload = record(raw.payload);
  if (!Number.isInteger(sequence) || sequence <= 0) return undefined;
  if (!KNOWN_REALTIME_EVENTS.has(eventType)) return undefined;
  if (!Number.isFinite(occurredAt) || occurredAt < 0 || !payload) return undefined;
  const aggregateId = raw.aggregate_id;
  if (aggregateId !== undefined && aggregateId !== null && typeof aggregateId !== 'string') return undefined;
  return {
    sequence,
    event_type: eventType,
    aggregate_id: typeof aggregateId === 'string' ? aggregateId : null,
    occurred_at: occurredAt,
    payload,
  };
}

function text(payload: Record<string, unknown>, key: string): string {
  return typeof payload[key] === 'string' ? payload[key] as string : '';
}

export function applyRealtimeEvent(
  event: RealtimeEvent,
  handlers: RealtimeProjectionHandlers,
): void {
  handlers.onEvent?.(event);
  const payload = event.payload;
  if (event.event_type === 'thread.updated') {
    const threadId = text(payload, 'thread_id') || event.aggregate_id || '';
    if (!threadId) return;
    handlers.patchThread?.(threadId, record(payload.projection));
    handlers.invalidateThread?.(threadId);
    return;
  }
  if (event.event_type === 'thread.removed') {
    const threadId = text(payload, 'thread_id') || event.aggregate_id || '';
    if (threadId) handlers.removeThread?.(threadId);
    return;
  }
  if (event.event_type === 'message.body_state') {
    const messageId = text(payload, 'message_id') || event.aggregate_id || '';
    if (messageId) handlers.invalidateBody?.(messageId);
    return;
  }
  if (event.event_type === 'auth.session_revoked') {
    handlers.authExpired?.();
    return;
  }
  if (event.event_type === 'system.version_changed') {
    const version = text(payload, 'version');
    if (version) handlers.versionChanged?.(version);
    return;
  }
  const scopeMap: Record<string, string[]> = {
    'account.updated': ['navigation', 'accounts'],
    'sync.state': ['sync'],
    'attachment.cache_state': ['attachments'],
    'draft.updated': ['drafts'],
    'send.state': ['drafts', 'send'],
    'operation.state': ['operations'],
    'conflict.created': ['operations', 'conflicts'],
    'conflict.resolved': ['operations', 'conflicts'],
    'cache.cleanup_state': ['settings'],
    'notification.created': ['notifications'],
    'notification.read': ['notifications'],
    'backup.state': ['backup'],
    'restore.validation_state': ['backup'],
    'admin.user_state': ['admin'],
  };
  const scopes = scopeMap[event.event_type];
  if (scopes) handlers.invalidateScopes?.(scopes);
}

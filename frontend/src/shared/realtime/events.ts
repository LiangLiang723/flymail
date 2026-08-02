import type { RealtimeEvent } from '../api/generated.ts';

export const KNOWN_REALTIME_EVENTS = new Set([
  'thread.created',
  'thread.updated',
  'thread.removed',
  'message.body_state',
  'operation.updated',
  'send.updated',
  'account.status_changed',
  'sync.updated',
  'conflict.created',
  'settings.updated',
  'session.revoked',
  'version.changed',
  'notification.created',
  'notification.updated',
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
  if (event.event_type === 'thread.created' || event.event_type === 'thread.updated') {
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
  if (event.event_type === 'session.revoked') {
    handlers.authExpired?.();
    return;
  }
  if (event.event_type === 'version.changed') {
    const version = text(payload, 'version');
    if (version) handlers.versionChanged?.(version);
    return;
  }
  const scopeMap: Record<string, string[]> = {
    'account.status_changed': ['navigation', 'accounts'],
    'sync.updated': ['sync'],
    'send.updated': ['drafts', 'send'],
    'operation.updated': ['operations'],
    'conflict.created': ['operations', 'conflicts'],
    'settings.updated': ['settings', 'navigation'],
    'notification.created': ['notifications'],
    'notification.updated': ['notifications'],
  };
  const scopes = scopeMap[event.event_type];
  if (scopes) handlers.invalidateScopes?.(scopes);
}

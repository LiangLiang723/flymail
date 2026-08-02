import type { RealtimeEvent } from '../api/generated.ts';
import { applyRealtimeEvent, decodeRealtimeEvent, type RealtimeProjectionHandlers } from './events.ts';

export type RealtimeState = 'connecting' | 'online' | 'reconnecting' | 'offline' | 'resync_required';

export interface RealtimeBatch {
  events: unknown[];
  current_sequence: number;
}

export type RealtimeEnvelope =
  | { type: 'events'; events: unknown[]; current_sequence: number }
  | { type: 'ping'; current_sequence: number }
  | { type: 'resync_required'; details?: { scopes?: unknown } };

interface RealtimeSocket {
  onopen: (() => void) | null;
  onmessage: ((event: { data: string }) => void) | null;
  onclose: ((event: { code: number }) => void) | null;
  onerror: (() => void) | null;
  close(code?: number): void;
}

export interface RealtimeClientOptions {
  initialSequence?: number;
  fetchBacklog?: (after: number) => Promise<RealtimeBatch>;
  handlers: RealtimeProjectionHandlers;
  socketFactory?: (url: string) => RealtimeSocket;
  visibilityState?: () => DocumentVisibilityState;
  setTimeoutFn?: typeof setTimeout;
  clearTimeoutFn?: typeof clearTimeout;
  random?: () => number;
}

function validScopes(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

export function reconnectDelay(attempt: number, random: () => number = Math.random): number {
  const base = Math.min(30_000, 1_000 * (2 ** Math.max(0, attempt)));
  const jitter = .75 + Math.max(0, Math.min(1, random())) * .5;
  return Math.min(30_000, Math.round(base * jitter));
}

export class RealtimeClient {
  private readonly fetchBacklog?: (after: number) => Promise<RealtimeBatch>;
  private readonly handlers: RealtimeProjectionHandlers;
  private readonly socketFactory?: (url: string) => RealtimeSocket;
  private readonly visibilityState: () => DocumentVisibilityState;
  private readonly setTimeoutFn: typeof setTimeout;
  private readonly clearTimeoutFn: typeof clearTimeout;
  private readonly random: () => number;
  private socket?: RealtimeSocket;
  private reconnectTimer?: ReturnType<typeof setTimeout>;
  private destroyed = false;
  private processingGap = false;
  state: RealtimeState = 'offline';
  sequence: number;
  reconnectAttempts = 0;

  constructor(options: RealtimeClientOptions) {
    this.sequence = Math.max(0, Number(options.initialSequence || 0));
    this.fetchBacklog = options.fetchBacklog;
    this.handlers = options.handlers;
    this.socketFactory = options.socketFactory || (typeof window === 'undefined' || typeof WebSocket === 'undefined'
      ? undefined
      : (url) => new WebSocket(url) as unknown as RealtimeSocket);
    this.visibilityState = options.visibilityState || (() => typeof document === 'undefined' ? 'visible' : document.visibilityState);
    this.setTimeoutFn = options.setTimeoutFn || setTimeout;
    this.clearTimeoutFn = options.clearTimeoutFn || clearTimeout;
    this.random = options.random || Math.random;
  }

  connect(afterSequence = this.sequence): void {
    if (this.destroyed || !this.socketFactory) return;
    this.cancelReconnect();
    this.sequence = Math.max(this.sequence, Math.max(0, Number(afterSequence || 0)));
    this.state = this.reconnectAttempts ? 'reconnecting' : 'connecting';
    const protocol = typeof location !== 'undefined' && location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = typeof location !== 'undefined' ? location.host : 'localhost';
    const socket = this.socketFactory(`${protocol}//${host}/api/v2/realtime?after=${this.sequence}`);
    this.socket = socket;
    socket.onopen = () => {
      if (this.socket !== socket) return;
      this.state = 'online';
      this.markStable();
    };
    socket.onmessage = (message) => {
      let envelope: unknown;
      try { envelope = JSON.parse(message.data); } catch { return; }
      void this.handleEnvelope(envelope);
    };
    socket.onerror = () => {
      if (this.state === 'online') this.state = 'reconnecting';
    };
    socket.onclose = (event) => {
      if (this.socket === socket) this.socket = undefined;
      this.handleClose(event.code);
    };
  }

  async handleEnvelope(value: unknown): Promise<void> {
    if (!value || typeof value !== 'object') return;
    const envelope = value as Partial<RealtimeEnvelope> & Record<string, unknown>;
    if (envelope.type === 'ping') {
      const current = Number(envelope.current_sequence || 0);
      if (Number.isFinite(current)) this.sequence = Math.max(this.sequence, current);
      this.state = 'online';
      return;
    }
    if (envelope.type === 'resync_required') {
      const details = envelope.details && typeof envelope.details === 'object'
        ? envelope.details as { scopes?: unknown }
        : undefined;
      const scopes = validScopes(details?.scopes);
      this.state = 'resync_required';
      this.handlers.invalidateScopes?.(scopes.length ? scopes : ['threads', 'navigation', 'sync']);
      return;
    }
    if (envelope.type !== 'events' || !Array.isArray(envelope.events)) return;
    for (const raw of envelope.events) {
      const decoded = decodeRealtimeEvent(raw);
      if (!decoded || decoded.sequence <= this.sequence) continue;
      if (decoded.sequence > this.sequence + 1) {
        const recovered = await this.recoverGap();
        if (!recovered || decoded.sequence > this.sequence + 1) {
          this.state = 'resync_required';
          this.handlers.invalidateScopes?.(['threads', 'navigation', 'sync']);
          return;
        }
      }
      this.apply(decoded);
    }
    this.state = 'online';
  }

  handleClose(code: number): void {
    if (this.destroyed) return;
    if (code === 4401) {
      this.state = 'offline';
      this.handlers.authExpired?.();
      return;
    }
    if (code === 4409) {
      this.state = 'resync_required';
      this.handlers.invalidateScopes?.(['threads', 'navigation', 'sync']);
      return;
    }
    this.state = 'reconnecting';
    this.handlers.statusFallback?.();
    this.recordReconnectAttempt();
    this.scheduleReconnect();
  }

  recordReconnectAttempt(): void {
    this.reconnectAttempts += 1;
  }

  markStable(): void {
    this.reconnectAttempts = 0;
  }

  destroy(): void {
    this.destroyed = true;
    this.cancelReconnect();
    this.socket?.close(1000);
    this.socket = undefined;
    this.state = 'offline';
  }

  private apply(event: RealtimeEvent): void {
    applyRealtimeEvent(event, this.handlers);
    this.sequence = event.sequence;
  }

  private async recoverGap(): Promise<boolean> {
    if (!this.fetchBacklog || this.processingGap) return false;
    this.processingGap = true;
    try {
      const batch = await this.fetchBacklog(this.sequence);
      for (const raw of batch.events) {
        const event = decodeRealtimeEvent(raw);
        if (!event || event.sequence <= this.sequence) continue;
        if (event.sequence !== this.sequence + 1) return false;
        this.apply(event);
      }
      return true;
    } finally {
      this.processingGap = false;
    }
  }

  private scheduleReconnect(): void {
    if (this.destroyed || !this.socketFactory || this.reconnectTimer) return;
    const hiddenDelay = this.visibilityState() === 'hidden' ? 15_000 : 0;
    const delay = Math.max(hiddenDelay, reconnectDelay(Math.max(0, this.reconnectAttempts - 1), this.random));
    this.reconnectTimer = this.setTimeoutFn(() => {
      this.reconnectTimer = undefined;
      this.connect();
    }, delay);
  }

  private cancelReconnect(): void {
    if (!this.reconnectTimer) return;
    this.clearTimeoutFn(this.reconnectTimer);
    this.reconnectTimer = undefined;
  }
}

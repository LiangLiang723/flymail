export type QueryKey = readonly unknown[];
export type QueryStatus = 'idle' | 'loading' | 'success' | 'error';

export interface QuerySnapshot<T> {
  data: T | undefined;
  status: QueryStatus;
  updatedAt: number;
  staleAt: number;
  error: unknown;
}

interface QueryEntry<T = unknown> extends QuerySnapshot<T> {
  inFlight?: Promise<T>;
  controller?: AbortController;
  subscribers: Set<() => void>;
}

export interface FetchOptions {
  staleMs?: number;
  staleWhileRevalidate?: boolean;
}

function stableValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableValue);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, stableValue(item)]),
    );
  }
  return value;
}

export function serializeQueryKey(userScope: string, key: QueryKey): string {
  return JSON.stringify([userScope, stableValue(key)]);
}

export class QueryCache {
  private readonly entries = new Map<string, QueryEntry>();
  private readonly now: () => number;
  private userScope: string;

  constructor(userScope = 'anonymous', now: () => number = () => Date.now()) {
    this.userScope = userScope;
    this.now = now;
  }

  setUserScope(userScope: string): void {
    const normalized = String(userScope || 'anonymous');
    if (normalized === this.userScope) return;
    this.clearUserData();
    this.userScope = normalized;
  }

  private id(key: QueryKey): string {
    return serializeQueryKey(this.userScope, key);
  }

  private entry<T>(key: QueryKey): QueryEntry<T> | undefined {
    return this.entries.get(this.id(key)) as QueryEntry<T> | undefined;
  }

  get<T>(key: QueryKey): T | undefined {
    return this.entry<T>(key)?.data;
  }

  snapshot<T>(key: QueryKey): QuerySnapshot<T> {
    const entry = this.entry<T>(key);
    return entry
      ? {
          data: entry.data,
          status: entry.status,
          updatedAt: entry.updatedAt,
          staleAt: entry.staleAt,
          error: entry.error,
        }
      : { data: undefined, status: 'idle', updatedAt: 0, staleAt: 0, error: undefined };
  }

  set<T>(key: QueryKey, data: T, staleMs = 0): T {
    const id = this.id(key);
    const previous = this.entries.get(id) as QueryEntry<T> | undefined;
    const timestamp = this.now();
    const next: QueryEntry<T> = previous || {
      data: undefined,
      status: 'idle',
      updatedAt: 0,
      staleAt: 0,
      error: undefined,
      subscribers: new Set(),
    };
    next.data = data;
    next.status = 'success';
    next.updatedAt = timestamp;
    next.staleAt = timestamp + Math.max(0, staleMs);
    next.error = undefined;
    this.entries.set(id, next);
    this.notify(next);
    return data;
  }

  fetch<T>(
    key: QueryKey,
    fetcher: (signal: AbortSignal) => Promise<T>,
    options: FetchOptions = {},
  ): Promise<T> {
    const staleMs = Math.max(0, options.staleMs ?? 0);
    const id = this.id(key);
    let entry = this.entries.get(id) as QueryEntry<T> | undefined;
    if (entry?.inFlight) return entry.inFlight;
    const hasData = entry?.data !== undefined;
    const fresh = hasData && this.now() < (entry?.staleAt || 0);
    if (fresh) return Promise.resolve(entry!.data as T);

    if (!entry) {
      entry = {
        data: undefined,
        status: 'idle',
        updatedAt: 0,
        staleAt: 0,
        error: undefined,
        subscribers: new Set(),
      };
      this.entries.set(id, entry);
    }

    const controller = new AbortController();
    entry.controller = controller;
    entry.status = hasData ? 'success' : 'loading';
    entry.error = undefined;
    const promise = fetcher(controller.signal)
      .then((data) => {
        if (controller.signal.aborted) throw new DOMException('aborted', 'AbortError');
        this.set(key, data, staleMs);
        return data;
      })
      .catch((error: unknown) => {
        const current = this.entries.get(id) as QueryEntry<T> | undefined;
        if (current) {
          current.status = current.data === undefined ? 'error' : 'success';
          current.error = error;
          this.notify(current);
        }
        throw error;
      })
      .finally(() => {
        const current = this.entries.get(id) as QueryEntry<T> | undefined;
        if (current?.inFlight === promise) {
          current.inFlight = undefined;
          current.controller = undefined;
        }
      });
    entry.inFlight = promise;
    this.notify(entry);

    if (hasData && options.staleWhileRevalidate) {
      void promise.catch(() => undefined);
      return Promise.resolve(entry.data as T);
    }
    return promise;
  }

  waitForIdle(key: QueryKey): Promise<unknown> {
    return this.entry(key)?.inFlight ?? Promise.resolve();
  }

  invalidate(key: QueryKey): void {
    const entry = this.entry(key);
    if (!entry) return;
    entry.staleAt = 0;
    this.notify(entry);
  }

  patch<T>(key: QueryKey, updater: (current: T) => T): T | undefined {
    const entry = this.entry<T>(key);
    if (!entry || entry.data === undefined) return undefined;
    entry.data = updater(entry.data);
    entry.updatedAt = this.now();
    this.notify(entry);
    return entry.data;
  }

  cancel(key: QueryKey): void {
    this.entry(key)?.controller?.abort();
  }

  subscribe(key: QueryKey, subscriber: () => void): () => void {
    const id = this.id(key);
    let entry = this.entries.get(id);
    if (!entry) {
      entry = {
        data: undefined,
        status: 'idle',
        updatedAt: 0,
        staleAt: 0,
        error: undefined,
        subscribers: new Set(),
      };
      this.entries.set(id, entry);
    }
    entry.subscribers.add(subscriber);
    return () => entry?.subscribers.delete(subscriber);
  }

  clearUserData(): void {
    for (const entry of this.entries.values()) entry.controller?.abort();
    this.entries.clear();
  }

  keys(): string[] {
    return [...this.entries.keys()];
  }

  private notify(entry: QueryEntry): void {
    for (const subscriber of entry.subscribers) subscriber();
  }
}

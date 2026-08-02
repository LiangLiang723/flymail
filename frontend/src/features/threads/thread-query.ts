import type { ThreadListResponse, ThreadProjection } from '../../shared/api/generated.ts';

function stable(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, stable(item)]),
    );
  }
  return value;
}

export function createThreadQueryKey(userId: string, descriptor: Record<string, unknown>): string {
  return JSON.stringify(['threads', userId, stable(descriptor)]);
}

export function appendThreadPage(
  current: ThreadProjection[],
  next: ThreadProjection[],
): ThreadProjection[] {
  if (!current.length) return [...next];
  const existing = new Set(current.map((item) => item.id));
  const appended = next.filter((item) => !existing.has(item.id));
  return appended.length ? [...current, ...appended] : current;
}

export function patchThreadProjection(
  threads: ThreadProjection[],
  threadId: string,
  patch: Partial<ThreadProjection>,
): ThreadProjection[] {
  let changed = false;
  const next = threads.map((thread) => {
    if (thread.id !== threadId) return thread;
    const candidate = { ...thread, ...patch };
    if (Object.keys(patch).every((key) => thread[key as keyof ThreadProjection] === candidate[key as keyof ThreadProjection])) {
      return thread;
    }
    changed = true;
    return candidate;
  });
  return changed ? next : threads;
}

export interface ThreadListLoadRequest {
  key: string;
  [key: string]: unknown;
}

export type ThreadListFetcher = (
  request: ThreadListLoadRequest,
  signal: AbortSignal,
) => Promise<ThreadListResponse>;

export class ThreadListController {
  private readonly fetcher: ThreadListFetcher;
  private controller?: AbortController;
  private generation = 0;
  current?: ThreadListResponse;

  constructor(fetcher: ThreadListFetcher) {
    this.fetcher = fetcher;
  }

  async load(request: ThreadListLoadRequest): Promise<ThreadListResponse> {
    this.controller?.abort();
    const generation = ++this.generation;
    const controller = new AbortController();
    this.controller = controller;
    try {
      const response = await this.fetcher(request, controller.signal);
      if (controller.signal.aborted) throw new DOMException('aborted', 'AbortError');
      if (generation !== this.generation) return response;
      this.current = response;
      return response;
    } finally {
      if (this.controller === controller) this.controller = undefined;
    }
  }

  cancel(): void {
    this.controller?.abort();
  }
}

export class ThreadCursorMemory {
  private readonly pages = new Map<string, { threads: ThreadProjection[]; nextCursor: string | null; touchedAt: number }>();
  private readonly maxEntries: number;

  constructor(maxEntries = 20) {
    this.maxEntries = Math.max(1, maxEntries);
  }

  get(key: string): ThreadListResponse | undefined {
    const entry = this.pages.get(key);
    if (!entry) return undefined;
    entry.touchedAt = Date.now();
    return { threads: entry.threads, next_cursor: entry.nextCursor };
  }

  set(key: string, response: ThreadListResponse, append = false): ThreadListResponse {
    const previous = this.pages.get(key);
    const threads = append && previous
      ? appendThreadPage(previous.threads, response.threads)
      : response.threads;
    this.pages.set(key, {
      threads,
      nextCursor: response.next_cursor || null,
      touchedAt: Date.now(),
    });
    this.trim();
    return { threads, next_cursor: response.next_cursor || null };
  }

  patch(threadId: string, patch: Partial<ThreadProjection>): void {
    for (const entry of this.pages.values()) {
      entry.threads = patchThreadProjection(entry.threads, threadId, patch);
    }
  }

  remove(threadId: string): void {
    for (const entry of this.pages.values()) {
      const next = entry.threads.filter((thread) => thread.id !== threadId);
      if (next.length !== entry.threads.length) entry.threads = next;
    }
  }

  clear(): void {
    this.pages.clear();
  }

  private trim(): void {
    if (this.pages.size <= this.maxEntries) return;
    const oldest = [...this.pages.entries()].sort((left, right) => left[1].touchedAt - right[1].touchedAt)[0];
    if (oldest) this.pages.delete(oldest[0]);
  }
}

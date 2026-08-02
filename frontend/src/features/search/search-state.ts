export interface SearchFilters {
  keyword?: string | null;
  from_addresses?: string[];
  to_addresses?: string[];
  date_from?: number | null;
  date_to?: number | null;
  account_ids?: string[];
  mailbox_ids?: string[];
  label_ids?: string[];
  is_read?: boolean | null;
  is_starred?: boolean | null;
  has_attachment?: boolean | null;
  min_size_bytes?: number | null;
  max_size_bytes?: number | null;
}

export interface SearchResultItem {
  thread_id: string;
  matched_message_id: string;
  matched_field: string;
  subject: string;
  snippet: string;
  received_at: number;
  account_ids: string[];
  unread: boolean;
  starred: boolean;
  has_attachment: boolean;
}

export interface SearchResponse {
  items: SearchResultItem[];
  next_cursor: string | null;
  fulltext_parser: string;
}

function strings(value: unknown, max = 50): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0).slice(0, max);
}
function optionalNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const number = Number(value);
  if (!Number.isFinite(number) || number < 0) throw new Error('invalid numeric search filter');
  return number;
}
function optionalBoolean(value: unknown): boolean | null {
  if (value === true || value === 'true' || value === '1') return true;
  if (value === false || value === 'false' || value === '0') return false;
  return null;
}

export function validateSearchFilters(value: unknown): SearchFilters {
  const raw = value && typeof value === 'object' ? value as Record<string, unknown> : {};
  const filters: SearchFilters = {
    keyword: typeof raw.keyword === 'string' ? raw.keyword.trim().slice(0, 512) || null : null,
    from_addresses: strings(raw.from_addresses),
    to_addresses: strings(raw.to_addresses),
    date_from: optionalNumber(raw.date_from),
    date_to: optionalNumber(raw.date_to),
    account_ids: strings(raw.account_ids),
    mailbox_ids: strings(raw.mailbox_ids),
    label_ids: strings(raw.label_ids),
    is_read: optionalBoolean(raw.is_read),
    is_starred: optionalBoolean(raw.is_starred),
    has_attachment: optionalBoolean(raw.has_attachment),
    min_size_bytes: optionalNumber(raw.min_size_bytes),
    max_size_bytes: optionalNumber(raw.max_size_bytes),
  };
  if (filters.date_from !== null && filters.date_to !== null && filters.date_from! > filters.date_to!) {
    throw new Error('date_from must not exceed date_to');
  }
  if (filters.min_size_bytes !== null && filters.max_size_bytes !== null && filters.min_size_bytes! > filters.max_size_bytes!) {
    throw new Error('min_size_bytes must not exceed max_size_bytes');
  }
  return Object.fromEntries(Object.entries(filters).filter(([, item]) => item !== null && (!Array.isArray(item) || item.length))) as SearchFilters;
}

export function serializeSearchFilters(filters: SearchFilters): Record<string, string> {
  const validated = validateSearchFilters(filters);
  const query: Record<string, string> = {};
  for (const [key, value] of Object.entries(validated)) {
    if (Array.isArray(value)) query[key] = value.join(',');
    else if (value !== null && value !== undefined) query[key] = String(value);
  }
  return query;
}

export function deserializeSearchFilters(query: Record<string, unknown>): SearchFilters {
  const normalized: Record<string, unknown> = { ...query };
  for (const key of ['from_addresses', 'to_addresses', 'account_ids', 'mailbox_ids', 'label_ids']) {
    if (typeof normalized[key] === 'string') normalized[key] = normalized[key].split(',').map((item) => item.trim()).filter(Boolean);
  }
  return validateSearchFilters(normalized);
}

export function appendSearchResults(current: SearchResultItem[], next: SearchResultItem[]): SearchResultItem[] {
  const byThread = new Map(current.map((item) => [item.thread_id, item]));
  const order = current.map((item) => item.thread_id);
  for (const item of next) {
    const previous = byThread.get(item.thread_id);
    if (!previous) order.push(item.thread_id);
    if (!previous || item.received_at >= previous.received_at) byThread.set(item.thread_id, item);
  }
  return order.map((threadId) => byThread.get(threadId)!).filter(Boolean);
}

export class SearchController {
  private readonly fetcher: (filters: SearchFilters, signal: AbortSignal, cursor?: string | null) => Promise<SearchResponse>;
  private readonly debounceMs: number;
  private controller?: AbortController;
  private timer?: ReturnType<typeof setTimeout>;

  constructor(fetcher: SearchController['fetcher'], debounceMs = 250) {
    this.fetcher = fetcher;
    this.debounceMs = Math.max(0, debounceMs);
  }

  search(filters: SearchFilters, cursor?: string | null): Promise<SearchResponse> {
    this.cancel();
    const validated = validateSearchFilters(filters);
    const controller = new AbortController();
    this.controller = controller;
    if (this.debounceMs === 0) return this.run(validated, controller, cursor);
    return new Promise<SearchResponse>((resolve, reject) => {
      this.timer = setTimeout(() => {
        this.timer = undefined;
        this.run(validated, controller, cursor).then(resolve, reject);
      }, this.debounceMs);
      controller.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true });
    });
  }

  cancel(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = undefined;
    this.controller?.abort();
    this.controller = undefined;
  }

  private async run(filters: SearchFilters, controller: AbortController, cursor?: string | null): Promise<SearchResponse> {
    try {
      return await this.fetcher(filters, controller.signal, cursor);
    } finally {
      if (this.controller === controller) this.controller = undefined;
    }
  }
}

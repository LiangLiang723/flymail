import type { ThreadListResponse, ThreadProjection } from '../../shared/api/generated.ts';

export type { ThreadListResponse, ThreadProjection };

export interface ThreadQueryDescriptor {
  scope: string;
  key: string;
  filters?: Record<string, unknown>;
  cursor?: string | null;
}

export interface ThreadListState {
  threads: ThreadProjection[];
  nextCursor: string | null;
  loading: boolean;
  refreshing: boolean;
  error?: string;
}

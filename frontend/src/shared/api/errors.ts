export interface ApiErrorEnvelope {
  error?: {
    code?: string;
    message?: string;
    request_id?: string;
    details?: unknown;
  };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string;
  readonly details: unknown;
  readonly retryable: boolean;

  constructor(options: {
    status?: number;
    code?: string;
    message?: string;
    requestId?: string;
    details?: unknown;
    retryable?: boolean;
  }) {
    super(options.message || '请求失败');
    this.name = 'ApiError';
    this.status = Number(options.status || 0);
    this.code = String(options.code || (this.status ? `http_${this.status}` : 'network_error'));
    this.requestId = String(options.requestId || '');
    this.details = options.details;
    this.retryable = Boolean(options.retryable);
  }
}

interface ErrorLike {
  status?: number;
  data?: unknown;
  response?: { status?: number; data?: unknown; headers?: Record<string, unknown> };
  code?: string;
  message?: string;
  name?: string;
}

export function normalizeApiError(value: unknown): ApiError {
  if (value instanceof ApiError) return value;
  if (value instanceof DOMException && value.name === 'AbortError') {
    return new ApiError({ code: 'aborted', message: '请求已取消' });
  }
  const candidate = (value && typeof value === 'object' ? value : {}) as ErrorLike;
  const response = candidate.response;
  const status = Number(response?.status ?? candidate.status ?? 0);
  const data = (response?.data ?? candidate.data) as ApiErrorEnvelope | undefined;
  const envelope = data?.error;
  const code = String(envelope?.code || candidate.code || (status ? `http_${status}` : 'network_error'));
  const message = String(envelope?.message || candidate.message || (status ? '请求失败' : '无法连接 FlyMail'));
  const retryable = status === 0 || status === 408 || status === 429 || status >= 500;
  return new ApiError({
    status,
    code,
    message,
    requestId: String(envelope?.request_id || ''),
    details: envelope?.details,
    retryable,
  });
}

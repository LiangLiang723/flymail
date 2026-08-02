import axios, { type AxiosRequestConfig } from 'axios';

import { ApiError, normalizeApiError } from './errors.ts';
import { QueryCache } from './query-cache.ts';

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export interface ApiRequest<T> {
  method: HttpMethod;
  path: string;
  query?: Record<string, string | number | boolean | undefined>;
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  idempotent?: boolean;
  responseType?: 'json' | 'blob' | 'arraybuffer' | 'text';
  _response?: T;
}

export interface TransportRequest {
  method: HttpMethod;
  url: string;
  headers: Record<string, string>;
  body?: unknown;
  signal?: AbortSignal;
  withCredentials: boolean;
  responseType?: 'json' | 'blob' | 'arraybuffer' | 'text';
}

export interface TransportResponse<T = unknown> {
  status: number;
  data: T;
  headers: Record<string, string>;
}

export type ApiTransport = (request: TransportRequest) => Promise<TransportResponse>;

export interface ApiClientOptions {
  baseUrl?: string;
  csrfToken?: () => string;
  cache?: QueryCache;
  transport?: ApiTransport;
}

function buildUrl(baseUrl: string, path: string, query?: ApiRequest<unknown>['query']): string {
  const joined = `${baseUrl.replace(/\/$/, '')}/${path.replace(/^\//, '')}`;
  if (!query) return joined;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) params.set(key, String(value));
  }
  const suffix = params.toString();
  return suffix ? `${joined}?${suffix}` : joined;
}

const axiosTransport: ApiTransport = async (request) => {
  const config: AxiosRequestConfig = {
    method: request.method,
    url: request.url,
    headers: request.headers,
    data: request.body,
    signal: request.signal,
    withCredentials: request.withCredentials,
    responseType: request.responseType,
    validateStatus: () => true,
  };
  const response = await axios.request(config);
  const headers: Record<string, string> = {};
  for (const [key, value] of Object.entries(response.headers || {})) {
    if (value !== undefined) headers[key.toLowerCase()] = String(value);
  }
  return { status: response.status, data: response.data, headers };
};

export class ApiClient {
  private readonly baseUrl: string;
  private readonly csrfToken: () => string;
  private readonly cache?: QueryCache;
  private readonly transport: ApiTransport;
  private readonly authExpiredListeners = new Set<() => void>();
  lastRequestId = '';

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = options.baseUrl || '';
    this.csrfToken = options.csrfToken || (() => '');
    this.cache = options.cache;
    this.transport = options.transport || axiosTransport;
  }

  onAuthExpired(listener: () => void): () => void {
    this.authExpiredListeners.add(listener);
    return () => this.authExpiredListeners.delete(listener);
  }

  async request<T>(request: ApiRequest<T>): Promise<T> {
    const method = request.method;
    const unsafe = !['GET'].includes(method);
    const headers: Record<string, string> = {
      Accept: 'application/json',
      ...request.headers,
    };
    if (unsafe) {
      const token = this.csrfToken();
      if (token) headers['X-CSRF-Token'] = token;
    }
    if (request.body !== undefined && !headers['Content-Type']) {
      headers['Content-Type'] = 'application/json';
    }
    const transportRequest: TransportRequest = {
      method,
      url: buildUrl(this.baseUrl, request.path, request.query),
      headers,
      body: request.body,
      signal: request.signal,
      withCredentials: true,
      responseType: request.responseType,
    };

    const mayRetry = method === 'GET' || request.idempotent === true;
    let attempt = 0;
    while (true) {
      try {
        const response = await this.transport(transportRequest);
        this.lastRequestId = response.headers['x-request-id'] || '';
        if (response.status >= 200 && response.status < 300) return response.data as T;
        throw normalizeApiError({ status: response.status, data: response.data });
      } catch (value: unknown) {
        if (value instanceof DOMException && value.name === 'AbortError') throw value;
        const error = normalizeApiError(value);
        if (error.status === 401) this.handleAuthExpired();
        if (mayRetry && attempt === 0 && error.retryable && !request.signal?.aborted) {
          attempt += 1;
          continue;
        }
        throw error;
      }
    }
  }

  private handleAuthExpired(): void {
    this.cache?.clearUserData();
    for (const listener of this.authExpiredListeners) listener();
  }
}

export const queryCache = new QueryCache();
let inMemoryCsrfToken = '';

export function setCsrfToken(token: string): void {
  inMemoryCsrfToken = String(token || '');
}

export function getCsrfToken(): string {
  return inMemoryCsrfToken;
}

export const apiClient = new ApiClient({
  cache: queryCache,
  csrfToken: () => inMemoryCsrfToken,
});

export { ApiError };

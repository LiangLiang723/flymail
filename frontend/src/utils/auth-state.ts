export interface ApiError {
  [key: string]: unknown;
  status?: number;
  status_code?: number;
  detail?: string;
  error?: string;
  message?: string;
  network?: boolean;
  code?: string;
}

export type AuthState = 'booting' | 'authenticated' | 'anonymous' | 'error';

function asRecord(value: unknown): Record<string, any> {
  return value && typeof value === 'object' ? value as Record<string, any> : {};
}

export function normalizeApiError(error: unknown): ApiError {
  const raw = asRecord(error);
  const response = asRecord(raw.response);
  const data = asRecord(response.data);
  const status = Number(response.status || raw.status || 0);
  const detail = String(data.detail || raw.detail || '');
  const backendError = String(data.error || raw.error || '');
  const message = String(detail || backendError || raw.message || '请求失败');
  const code = raw.code ? String(raw.code) : undefined;
  const network = !status || code === 'ECONNABORTED' || code === 'ERR_NETWORK';
  const compatibilityFields = Object.keys(data).length > 0
    ? data
    : raw.response
      ? {}
      : raw;

  return {
    ...compatibilityFields,
    status,
    detail,
    message,
    network,
    ...(code ? { code } : {}),
  };
}

export function classifyAuthError(error: ApiError): 'anonymous' | 'error' {
  return error.status === 401 || error.status === 403 ? 'anonymous' : 'error';
}

export function getLoginErrorMessage(error: ApiError): string {
  if (error.status === 401) return '用户名或密码错误';
  if (error.status === 403) return '此账号已被禁用，请联系管理员';
  if (error.network || !error.status) return '暂时无法连接 FlyMail，请稍后重试';
  return error.detail || error.message || '登录失败，请稍后重试';
}

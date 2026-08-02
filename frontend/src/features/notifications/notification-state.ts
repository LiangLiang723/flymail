export function configuredSecret(configured: boolean, _value?: string): string {
  return configured ? '已配置' : '';
}

export function clearSecretPayload<T extends Record<string, unknown>>(payload: T, secretKeys: string[]): T {
  const next: Record<string, unknown> = { ...payload };
  for (const key of secretKeys) {
    if (typeof next[key] === 'string' && !String(next[key]).trim()) delete next[key];
  }
  return next as T;
}

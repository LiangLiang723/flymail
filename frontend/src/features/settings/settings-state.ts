export const DEFAULT_BODY_QUOTA_BYTES = 5 * 1024 ** 3;

export function formatQuota(bytes: number): string {
  if (bytes === 0) return '不限额';
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(bytes % 1024 ** 3 ? 1 : 0)} GB`;
  return `${Math.round(bytes / 1024 ** 2)} MB`;
}

export function quotaDecreaseNeedsCleanup(previous: number, next: number): boolean {
  if (next === 0) return false;
  return previous === 0 || next < previous;
}

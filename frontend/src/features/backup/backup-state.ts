export async function clearSecretAfter<T>(
  read: () => string,
  clear: (value: string) => void,
  action: (secret: string) => Promise<T>,
): Promise<T> {
  const secret = read();
  try {
    return await action(secret);
  } finally {
    clear('');
  }
}

export function restoreReviewItems(counts: { pending_sends?: number; pending_remote_operations?: number }) {
  return [
    { kind: 'pending_send', count: Number(counts.pending_sends || 0), state: 'review_required', automatic: false },
    { kind: 'remote_operation', count: Number(counts.pending_remote_operations || 0), state: 'review_required', automatic: false },
  ].filter((item) => item.count > 0);
}

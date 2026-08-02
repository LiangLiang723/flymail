const pendingOAuth = new Map<string, { expiresAt: number }>();

export function rememberOAuthStart(state: string, expiresAt: number): void {
  pendingOAuth.set(state, { expiresAt });
}

export function readOAuthStart(state: string): { expiresAt: number } | undefined {
  return pendingOAuth.get(state);
}

export function forgetOAuthStart(state: string): void {
  pendingOAuth.delete(state);
}

export function validateOAuthCallback(options: {
  expectedState: string;
  returnedState: string;
  expiresAt: number;
  now?: number;
  status: string;
}): { ok: boolean; reason?: 'state_mismatch' | 'expired' | 'cancelled' | 'failed' } {
  if (options.status === 'cancelled') return { ok: false, reason: 'cancelled' };
  if (options.expectedState !== options.returnedState) return { ok: false, reason: 'state_mismatch' };
  if (options.expiresAt <= (options.now ?? Date.now())) return { ok: false, reason: 'expired' };
  if (options.status !== 'success') return { ok: false, reason: 'failed' };
  return { ok: true };
}

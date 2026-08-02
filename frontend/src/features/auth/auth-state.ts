import { reactive, readonly } from 'vue';

import { apiClient, setCsrfToken } from '../../shared/api/client.ts';
import type { AuthResponse } from '../../shared/api/generated.ts';
import { ApiError, normalizeApiError } from '../../shared/api/errors.ts';
import { bootstrapController } from '../../app/bootstrap.ts';

interface AuthState {
  submitting: boolean;
  error?: ApiError;
}

const state = reactive<AuthState>({ submitting: false });

export function useAuthState() {
  return {
    state: readonly(state),
    async login(username: string, password: string): Promise<boolean> {
      state.submitting = true;
      state.error = undefined;
      try {
        const result = await apiClient.request<AuthResponse>({
          method: 'POST',
          path: '/api/v2/auth/login',
          body: { username, password },
        });
        setCsrfToken(result.csrf_token);
        await bootstrapController.load(true);
        return true;
      } catch (value: unknown) {
        state.error = normalizeApiError(value);
        return false;
      } finally {
        state.submitting = false;
      }
    },
    async logout(): Promise<void> {
      try {
        await apiClient.request({ method: 'POST', path: '/api/v2/auth/logout' });
      } finally {
        bootstrapController.clear();
      }
    },
  };
}

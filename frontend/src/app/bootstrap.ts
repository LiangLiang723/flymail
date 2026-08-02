import { reactive, readonly } from 'vue';

import { apiClient, queryCache, setCsrfToken } from '../shared/api/client.ts';
import type { BootstrapResponse } from '../shared/api/generated.ts';
import { ApiError, normalizeApiError } from '../shared/api/errors.ts';

export type BootstrapPhase = 'checking' | 'authenticated' | 'anonymous' | 'network_error' | 'maintenance' | 'incompatible';

export interface BootstrapState {
  phase: BootstrapPhase;
  data?: BootstrapResponse;
  error?: ApiError;
}

export interface BootstrapController {
  readonly state: BootstrapState;
  load(force?: boolean): Promise<BootstrapResponse | undefined>;
  clear(): void;
}

export function createBootstrapController(
  loader: () => Promise<BootstrapResponse> = () => apiClient.request<BootstrapResponse>({ method: 'GET', path: '/api/v2/bootstrap' }),
): BootstrapController {
  const state = reactive<BootstrapState>({ phase: 'checking' });
  let inFlight: Promise<BootstrapResponse | undefined> | undefined;

  const load = (force = false): Promise<BootstrapResponse | undefined> => {
    if (inFlight && !force) return inFlight;
    if (state.phase === 'authenticated' && state.data && !force) return Promise.resolve(state.data);
    state.phase = 'checking';
    state.error = undefined;
    inFlight = loader()
      .then((data) => {
        state.data = data;
        state.phase = 'authenticated';
        setCsrfToken(data.csrf_token);
        queryCache.setUserScope(data.user.id);
        return data;
      })
      .catch((value: unknown) => {
        const error = normalizeApiError(value);
        state.data = undefined;
        state.error = error;
        setCsrfToken('');
        queryCache.setUserScope('anonymous');
        if (error.status === 401 || error.status === 403) state.phase = 'anonymous';
        else if (error.status === 503) state.phase = 'maintenance';
        else if (error.code === 'version_incompatible') state.phase = 'incompatible';
        else state.phase = 'network_error';
        return undefined;
      })
      .finally(() => { inFlight = undefined; });
    return inFlight;
  };

  return {
    state: readonly(state) as BootstrapState,
    load,
    clear() {
      state.phase = 'anonymous';
      state.data = undefined;
      state.error = undefined;
      setCsrfToken('');
      queryCache.setUserScope('anonymous');
    },
  };
}

export const bootstrapController = createBootstrapController();

export function useBootstrap(): BootstrapController {
  return bootstrapController;
}

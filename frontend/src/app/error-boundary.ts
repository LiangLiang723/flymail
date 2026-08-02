import { reactive, readonly } from 'vue';

export interface ErrorBoundaryState {
  hasError: boolean;
  message: string;
}

export function createErrorBoundaryState(retryAction: () => Promise<void> | void) {
  const state = reactive<ErrorBoundaryState>({ hasError: false, message: '' });
  return {
    state: readonly(state) as ErrorBoundaryState,
    capture(value: unknown): void {
      state.hasError = true;
      state.message = value instanceof Error ? value.message : '页面加载失败';
    },
    clear(): void {
      state.hasError = false;
      state.message = '';
    },
    async retry(): Promise<void> {
      await retryAction();
      state.hasError = false;
      state.message = '';
    },
  };
}

export const THEME_STORAGE_KEY = 'flymail_theme';

export type ThemePreference = 'system' | 'light' | 'dark';
export type ResolvedTheme = 'light' | 'dark';

export interface ThemeStorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export interface ThemeRootLike {
  classList: {
    toggle(token: string, force?: boolean): boolean;
  };
  style: {
    colorScheme?: string;
  };
}

export interface MediaQueryListLike {
  matches: boolean;
  addEventListener(type: 'change', listener: (event: { matches: boolean }) => void): void;
  removeEventListener(type: 'change', listener: (event: { matches: boolean }) => void): void;
}

interface ThemeEnvironment {
  storage: ThemeStorageLike;
  root: ThemeRootLike;
  mediaQuery: MediaQueryListLike;
}

export interface ThemeController {
  initialize(): ThemePreference;
  destroy(): void;
  getPreference(): ThemePreference;
  setPreference(preference: ThemePreference): void;
}

export function normalizeThemePreference(value: string | null | undefined): ThemePreference {
  if (value === 'light' || value === 'dark') return value;
  return 'system';
}

export function resolveTheme(preference: ThemePreference, systemDark: boolean): ResolvedTheme {
  if (preference === 'system') return systemDark ? 'dark' : 'light';
  return preference;
}

export function createThemeController(environment: ThemeEnvironment): ThemeController {
  let preference: ThemePreference = 'system';
  let initialized = false;

  function applyTheme() {
    const resolved = resolveTheme(preference, environment.mediaQuery.matches);
    environment.root.classList.toggle('dark', resolved === 'dark');
    environment.root.style.colorScheme = resolved;
  }

  function handleSystemThemeChange() {
    if (preference === 'system') applyTheme();
  }

  return {
    initialize() {
      preference = normalizeThemePreference(environment.storage.getItem(THEME_STORAGE_KEY));
      if (!initialized) {
        environment.mediaQuery.addEventListener('change', handleSystemThemeChange);
        initialized = true;
      }
      applyTheme();
      return preference;
    },
    destroy() {
      if (!initialized) return;
      environment.mediaQuery.removeEventListener('change', handleSystemThemeChange);
      initialized = false;
    },
    getPreference() {
      return preference;
    },
    setPreference(nextPreference) {
      preference = nextPreference;
      environment.storage.setItem(THEME_STORAGE_KEY, preference);
      applyTheme();
    },
  };
}

function createBrowserThemeController(): ThemeController {
  if (typeof window === 'undefined' || typeof document === 'undefined') {
    const values = new Map<string, string>();
    return createThemeController({
      storage: {
        getItem: (key) => values.get(key) ?? null,
        setItem: (key, value) => values.set(key, value),
      },
      root: {
        classList: { toggle: () => false },
        style: {},
      },
      mediaQuery: {
        matches: false,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
      },
    });
  }

  return createThemeController({
    storage: window.localStorage,
    root: document.documentElement,
    mediaQuery: window.matchMedia('(prefers-color-scheme: dark)'),
  });
}

export const themeController = createBrowserThemeController();

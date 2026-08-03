export type ThemePreference = 'system' | 'light' | 'dark';
export type DensityPreference = 'comfortable' | 'compact';

export interface AppearancePreferences {
  theme?: ThemePreference;
  density?: DensityPreference;
}

const systemDarkQuery = typeof window === 'undefined'
  ? undefined
  : window.matchMedia('(prefers-color-scheme: dark)');
let activeRoot: HTMLElement | undefined;
let activePreferences: AppearancePreferences | null | undefined;

function resolvedDark(preferences: AppearancePreferences | null | undefined): boolean {
  const theme = preferences?.theme || 'system';
  if (theme === 'dark') return true;
  if (theme === 'light') return false;
  return Boolean(systemDarkQuery?.matches);
}

function syncAppearance(
  preferences: AppearancePreferences | null | undefined,
  root: HTMLElement,
): void {
  const theme = preferences?.theme || 'system';
  if (theme === 'light' || theme === 'dark') root.dataset.theme = theme;
  else delete root.dataset.theme;
  root.classList.toggle('dark', resolvedDark(preferences));
  root.dataset.density = preferences?.density === 'compact' ? 'compact' : 'comfortable';
}

systemDarkQuery?.addEventListener('change', () => {
  if (activeRoot && (activePreferences?.theme || 'system') === 'system') {
    syncAppearance(activePreferences, activeRoot);
  }
});

export function applyAppearance(
  preferences: AppearancePreferences | null | undefined,
  root: HTMLElement = document.documentElement,
): void {
  activeRoot = root;
  activePreferences = preferences;
  syncAppearance(preferences, root);
}

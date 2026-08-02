export type ThemePreference = 'system' | 'light' | 'dark';
export type DensityPreference = 'comfortable' | 'compact';

export interface AppearancePreferences {
  theme?: ThemePreference;
  density?: DensityPreference;
}

export function applyAppearance(
  preferences: AppearancePreferences | null | undefined,
  root: HTMLElement = document.documentElement,
): void {
  const theme = preferences?.theme || 'system';
  if (theme === 'light' || theme === 'dark') root.dataset.theme = theme;
  else delete root.dataset.theme;
  root.dataset.density = preferences?.density === 'compact' ? 'compact' : 'comfortable';
}

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  createThemeController,
  normalizeThemePreference,
  resolveTheme,
  type MediaQueryListLike,
  type ThemeRootLike,
  type ThemeStorageLike,
} from '../src/utils/theme.ts';

class FakeStorage implements ThemeStorageLike {
  private values = new Map<string, string>();

  constructor(initial?: Record<string, string>) {
    Object.entries(initial || {}).forEach(([key, value]) => this.values.set(key, value));
  }

  getItem(key: string) {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string) {
    this.values.set(key, value);
  }
}

class FakeRoot implements ThemeRootLike {
  classes = new Set<string>();
  style: { colorScheme?: string } = {};
  classList = {
    toggle: (token: string, force?: boolean) => {
      const enabled = force ?? !this.classes.has(token);
      if (enabled) this.classes.add(token);
      else this.classes.delete(token);
      return enabled;
    },
  };
}

class FakeMediaQuery implements MediaQueryListLike {
  matches: boolean;
  private listeners = new Set<(event: { matches: boolean }) => void>();

  constructor(matches: boolean) {
    this.matches = matches;
  }

  addEventListener(_type: 'change', listener: (event: { matches: boolean }) => void) {
    this.listeners.add(listener);
  }

  removeEventListener(_type: 'change', listener: (event: { matches: boolean }) => void) {
    this.listeners.delete(listener);
  }

  setMatches(matches: boolean) {
    this.matches = matches;
    this.listeners.forEach((listener) => listener({ matches }));
  }
}

function createFixture(initialPreference?: string, systemDark = false) {
  const storage = new FakeStorage(initialPreference ? { flymail_theme: initialPreference } : undefined);
  const root = new FakeRoot();
  const mediaQuery = new FakeMediaQuery(systemDark);
  const controller = createThemeController({ storage, root, mediaQuery });
  return { controller, storage, root, mediaQuery };
}

test('normalizes missing and invalid preferences to system', () => {
  assert.equal(normalizeThemePreference(null), 'system');
  assert.equal(normalizeThemePreference('unknown'), 'system');
  assert.equal(normalizeThemePreference('light'), 'light');
  assert.equal(normalizeThemePreference('dark'), 'dark');
});

test('resolves explicit and system theme preferences', () => {
  assert.equal(resolveTheme('system', false), 'light');
  assert.equal(resolveTheme('system', true), 'dark');
  assert.equal(resolveTheme('light', true), 'light');
  assert.equal(resolveTheme('dark', false), 'dark');
});

test('initializes from persisted preference and updates the root theme', () => {
  const { controller, root } = createFixture('dark', false);

  controller.initialize();

  assert.equal(controller.getPreference(), 'dark');
  assert.equal(root.classes.has('dark'), true);
  assert.equal(root.style.colorScheme, 'dark');
});

test('persists manual changes and applies them immediately', () => {
  const { controller, storage, root } = createFixture(undefined, true);
  controller.initialize();

  controller.setPreference('light');

  assert.equal(storage.getItem('flymail_theme'), 'light');
  assert.equal(root.classes.has('dark'), false);
  assert.equal(root.style.colorScheme, 'light');
});

test('system preference follows media-query changes', () => {
  const { controller, root, mediaQuery } = createFixture('system', false);
  controller.initialize();

  mediaQuery.setMatches(true);
  assert.equal(root.classes.has('dark'), true);
  assert.equal(root.style.colorScheme, 'dark');

  mediaQuery.setMatches(false);
  assert.equal(root.classes.has('dark'), false);
  assert.equal(root.style.colorScheme, 'light');
});

test('explicit preference ignores later system changes', () => {
  const { controller, root, mediaQuery } = createFixture('dark', false);
  controller.initialize();

  mediaQuery.setMatches(false);
  assert.equal(root.classes.has('dark'), true);

  controller.destroy();
});

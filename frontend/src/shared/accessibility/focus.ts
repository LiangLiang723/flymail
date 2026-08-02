export interface FocusableTarget {
  focus: (options?: FocusOptions) => void;
  isConnected?: boolean;
}

interface EditingTarget {
  tagName?: string;
  isContentEditable?: boolean;
  getAttribute?: (name: string) => string | null;
}

interface ShortcutEventLike {
  target: unknown;
  defaultPrevented: boolean;
  isComposing?: boolean;
}

const EDITING_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT']);
const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

export function isTextEditingTarget(target: unknown): boolean {
  if (!target || typeof target !== 'object') return false;
  const value = target as EditingTarget;
  const tagName = String(value.tagName || '').toUpperCase();
  if (EDITING_TAGS.has(tagName)) return true;
  if (value.isContentEditable) return true;
  return value.getAttribute?.('role') === 'textbox';
}

export function shouldHandleShortcut(event: ShortcutEventLike): boolean {
  return !event.defaultPrevented && !event.isComposing && !isTextEditingTarget(event.target);
}

export function nextRovingIndex(current: number, delta: number, count: number): number {
  if (!Number.isInteger(count) || count <= 0) return -1;
  const normalized = Number.isInteger(current) && current >= 0 ? current % count : 0;
  return (normalized + delta % count + count) % count;
}

export class FocusReturnStack {
  private readonly items: FocusableTarget[] = [];

  push(target: FocusableTarget | null | undefined): void {
    if (target && typeof target.focus === 'function') this.items.push(target);
  }

  restore(): boolean {
    while (this.items.length) {
      const target = this.items.pop();
      if (!target || target.isConnected === false) continue;
      target.focus({ preventScroll: true });
      return true;
    }
    return false;
  }
}

export function createFocusTrap(
  container: HTMLElement,
  returnFocus: FocusableTarget | null = document.activeElement as HTMLElement | null,
): () => void {
  const returnStack = new FocusReturnStack();
  returnStack.push(returnFocus);

  const focusable = (): HTMLElement[] => Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
    .filter((item) => item.getAttribute('aria-hidden') !== 'true' && item.offsetParent !== null);

  const onKeydown = (event: KeyboardEvent) => {
    if (event.key !== 'Tab') return;
    const items = focusable();
    if (!items.length) {
      event.preventDefault();
      container.focus({ preventScroll: true });
      return;
    }
    const activeIndex = items.indexOf(document.activeElement as HTMLElement);
    const delta = event.shiftKey ? -1 : 1;
    const next = nextRovingIndex(activeIndex < 0 ? 0 : activeIndex, delta, items.length);
    if (activeIndex < 0 || (event.shiftKey && activeIndex === 0) || (!event.shiftKey && activeIndex === items.length - 1)) {
      event.preventDefault();
      items[next]?.focus({ preventScroll: true });
    }
  };

  container.addEventListener('keydown', onKeydown);
  queueMicrotask(() => (focusable()[0] || container).focus({ preventScroll: true }));

  return () => {
    container.removeEventListener('keydown', onKeydown);
    returnStack.restore();
  };
}

export type SelectionMode = 'browsing' | 'selecting';

export function shouldHandleListShortcut(target: { tagName?: string; isContentEditable?: boolean } | null, key: string): boolean {
  if (!['ArrowUp', 'ArrowDown', 'Enter', ' '].includes(key)) return false;
  const tagName = String(target?.tagName || '').toUpperCase();
  if (target?.isContentEditable) return false;
  return !['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON'].includes(tagName);
}

export function createThreadSelection(initialIds: string[] = []) {
  let ids = [...initialIds];
  let focusIndex = ids.length ? 0 : -1;
  let mode: SelectionMode = 'browsing';
  const selected = new Set<string>();

  const normalizeFocus = () => {
    if (!ids.length) focusIndex = -1;
    else focusIndex = Math.max(0, Math.min(focusIndex, ids.length - 1));
  };

  return {
    get mode() { return mode; },
    get focusedId() { return focusIndex >= 0 ? ids[focusIndex] : undefined; },
    get selectedIds() { return [...selected]; },
    replace(nextIds: string[]) {
      const previousFocused = focusIndex >= 0 ? ids[focusIndex] : undefined;
      ids = [...nextIds];
      focusIndex = previousFocused ? ids.indexOf(previousFocused) : (ids.length ? 0 : -1);
      normalizeFocus();
      for (const item of [...selected]) if (!ids.includes(item)) selected.delete(item);
      if (!selected.size) mode = 'browsing';
    },
    move(delta: number) {
      if (!ids.length) return undefined;
      focusIndex = Math.max(0, Math.min(ids.length - 1, focusIndex + delta));
      return ids[focusIndex];
    },
    focus(id: string) {
      const index = ids.indexOf(id);
      if (index >= 0) focusIndex = index;
    },
    toggle(id: string) {
      if (!ids.includes(id)) return;
      if (selected.has(id)) selected.delete(id);
      else selected.add(id);
      mode = selected.size ? 'selecting' : 'browsing';
      this.focus(id);
    },
    enterMobileSelection(id: string) {
      selected.add(id);
      mode = 'selecting';
      this.focus(id);
    },
    clear() {
      selected.clear();
      mode = 'browsing';
    },
    remove(id: string) {
      const index = ids.indexOf(id);
      if (index < 0) return;
      ids.splice(index, 1);
      selected.delete(id);
      if (focusIndex > index) focusIndex -= 1;
      else if (focusIndex === index) focusIndex = Math.min(index, ids.length - 1);
      normalizeFocus();
      if (!selected.size) mode = 'browsing';
    },
  };
}

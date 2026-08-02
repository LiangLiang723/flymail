export function createImageViewerState(images: string[], initialIndex = 0) {
  const items = [...images];
  let index = items.length ? Math.max(0, Math.min(initialIndex, items.length - 1)) : -1;
  let scale = 1;
  let offset = { x: 0, y: 0 };

  const resetTransform = () => {
    scale = 1;
    offset = { x: 0, y: 0 };
  };

  return {
    get current() { return index >= 0 ? items[index] : undefined; },
    get index() { return index; },
    get scale() { return scale; },
    get offset() { return { ...offset }; },
    get count() { return items.length; },
    zoomBy(delta: number) {
      scale = Math.max(1, Math.min(4, scale + delta));
      if (scale === 1) offset = { x: 0, y: 0 };
    },
    setScale(next: number) {
      scale = Math.max(1, Math.min(4, next));
      if (scale === 1) offset = { x: 0, y: 0 };
    },
    dragBy(x: number, y: number) {
      if (scale <= 1) return;
      offset = { x: offset.x + x, y: offset.y + y };
    },
    next() {
      if (!items.length) return;
      index = (index + 1) % items.length;
      resetTransform();
    },
    previous() {
      if (!items.length) return;
      index = (index - 1 + items.length) % items.length;
      resetTransform();
    },
    swipe(deltaX: number, deltaY: number) {
      if (Math.abs(deltaX) < 48 || Math.abs(deltaX) <= Math.abs(deltaY)) return false;
      if (deltaX < 0) this.next();
      else this.previous();
      return true;
    },
  };
}

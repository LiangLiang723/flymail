export const MIN_IMAGE_SCALE = 1;
export const MAX_IMAGE_SCALE = 5;

export function clampScale(value: number): number {
  if (!Number.isFinite(value)) return MIN_IMAGE_SCALE;
  return Math.min(MAX_IMAGE_SCALE, Math.max(MIN_IMAGE_SCALE, value));
}

export function nextImageIndex(current: number, direction: -1 | 1, total: number): number {
  if (total <= 0) return 0;
  return (current + direction + total) % total;
}

export function shouldChangeImageFromSwipe(
  horizontalDistance: number,
  verticalDistance: number,
  durationMs: number,
): boolean {
  return Math.abs(horizontalDistance) >= 72
    && Math.abs(horizontalDistance) > Math.abs(verticalDistance) * 1.4
    && durationMs <= 650;
}

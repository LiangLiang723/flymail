export const MIN_EDITOR_IMAGE_WIDTH = 80;

export function parseImageWidth(value: unknown): number | null {
  const match = String(value ?? '').trim().match(/^(\d+)(?:px)?$/i);
  if (!match) return null;
  const width = Number(match[1]);
  return Number.isFinite(width) && width > 0 ? Math.round(width) : null;
}

export function clampImageWidth(
  value: number,
  containerWidth: number,
  minimum = MIN_EDITOR_IMAGE_WIDTH,
): number {
  const safeContainer = Math.max(1, Math.round(containerWidth || 0));
  const safeMinimum = Math.min(Math.max(1, Math.round(minimum)), safeContainer);
  return Math.min(safeContainer, Math.max(safeMinimum, Math.round(value || safeMinimum)));
}

export function imageWidthForInitialRender(value: number, containerWidth: number): number {
  const persistedWidth = Math.max(1, Math.round(value || 1));
  const measuredWidth = Math.max(0, Math.round(containerWidth || 0));
  if (measuredWidth <= 1) return persistedWidth;
  return Math.min(persistedWidth, measuredWidth);
}

export function imageWidthFromPercent(
  containerWidth: number,
  percent: number,
  minimum = MIN_EDITOR_IMAGE_WIDTH,
): number {
  return clampImageWidth(containerWidth * percent / 100, containerWidth, minimum);
}

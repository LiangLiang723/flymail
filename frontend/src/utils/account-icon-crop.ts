export interface CropState {
  scale: number;
  offsetX: number;
  offsetY: number;
}

export const ACCOUNT_ICON_OUTPUT_SIZE = 256;

const clamp = (value: number, minimum: number, maximum: number) => Math.min(maximum, Math.max(minimum, value));

export function coverScale(imageWidth: number, imageHeight: number, viewportSize: number): number {
  if (imageWidth <= 0 || imageHeight <= 0 || viewportSize <= 0) return 1;
  return Math.max(viewportSize / imageWidth, viewportSize / imageHeight);
}

export function clampCropState(
  state: CropState,
  imageWidth: number,
  imageHeight: number,
  viewportSize: number,
): CropState {
  const minimumScale = coverScale(imageWidth, imageHeight, viewportSize);
  const maximumScale = minimumScale * 5;
  const scale = clamp(Number.isFinite(state.scale) ? state.scale : minimumScale, minimumScale, maximumScale);
  const maximumOffsetX = Math.max(0, (imageWidth * scale - viewportSize) / 2);
  const maximumOffsetY = Math.max(0, (imageHeight * scale - viewportSize) / 2);
  return {
    scale,
    offsetX: clamp(Number.isFinite(state.offsetX) ? state.offsetX : 0, -maximumOffsetX, maximumOffsetX),
    offsetY: clamp(Number.isFinite(state.offsetY) ? state.offsetY : 0, -maximumOffsetY, maximumOffsetY),
  };
}

export function pinchScale(
  startScale: number,
  startDistance: number,
  currentDistance: number,
  minimum = 0.1,
  maximum = 5,
): number {
  if (startDistance <= 0 || currentDistance <= 0) return clamp(startScale, minimum, maximum);
  return clamp(startScale * (currentDistance / startDistance), minimum, maximum);
}

export async function renderAccountIconBlob(
  image: HTMLImageElement,
  state: CropState,
  viewportSize: number,
): Promise<Blob> {
  const naturalWidth = image.naturalWidth;
  const naturalHeight = image.naturalHeight;
  const crop = clampCropState(state, naturalWidth, naturalHeight, viewportSize);
  const sourceSize = viewportSize / crop.scale;
  const sourceX = naturalWidth / 2 - sourceSize / 2 - crop.offsetX / crop.scale;
  const sourceY = naturalHeight / 2 - sourceSize / 2 - crop.offsetY / crop.scale;

  const canvas = document.createElement('canvas');
  canvas.width = ACCOUNT_ICON_OUTPUT_SIZE;
  canvas.height = ACCOUNT_ICON_OUTPUT_SIZE;
  const context = canvas.getContext('2d');
  if (!context) throw new Error('无法生成裁剪图片');
  context.imageSmoothingEnabled = true;
  context.imageSmoothingQuality = 'high';
  context.drawImage(
    image,
    sourceX,
    sourceY,
    sourceSize,
    sourceSize,
    0,
    0,
    ACCOUNT_ICON_OUTPUT_SIZE,
    ACCOUNT_ICON_OUTPUT_SIZE,
  );
  const blob = await new Promise<Blob | null>((resolve) => {
    canvas.toBlob(resolve, 'image/webp', 0.9);
  });
  if (!blob) throw new Error('无法生成裁剪图片');
  return blob;
}

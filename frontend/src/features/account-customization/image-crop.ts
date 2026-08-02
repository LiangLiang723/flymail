export interface SquareCrop {
  x: number;
  y: number;
  size: number;
  width: number;
  height: number;
  orientation: number;
}

export function normalizeSquareCrop(crop: SquareCrop): SquareCrop {
  const maximum = Math.max(1, Math.min(crop.width, crop.height));
  const size = Math.max(1, Math.min(crop.size, maximum));
  const x = Math.max(0, Math.min(crop.x, crop.width - size));
  const y = Math.max(0, Math.min(crop.y, crop.height - size));
  return { ...crop, x, y, size };
}

export async function cropImageToBlob(file: File, crop: SquareCrop, outputSize = 256): Promise<Blob> {
  const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
  try {
    const normalized = normalizeSquareCrop(crop);
    const canvas = document.createElement('canvas');
    canvas.width = outputSize;
    canvas.height = outputSize;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('canvas is unavailable');
    context.drawImage(bitmap, normalized.x, normalized.y, normalized.size, normalized.size, 0, 0, outputSize, outputSize);
    return await new Promise<Blob>((resolve, reject) => canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error('image encode failed')), 'image/webp', .9));
  } finally {
    bitmap.close();
  }
}

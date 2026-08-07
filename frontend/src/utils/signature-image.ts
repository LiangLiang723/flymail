const MANAGED_SIGNATURE_IMAGE_PREFIX = 'flymail-signature-image:';
const SIGNATURE_IMAGE_ID_RE = /^[0-9a-f]{24}\.[0-9a-f]{32}$/i;

export function normalizeSignatureImageId(value: unknown): string | null {
  const normalized = String(value ?? '').trim().toLowerCase();
  return SIGNATURE_IMAGE_ID_RE.test(normalized) ? normalized : null;
}

export function managedSignatureImageSource(imageId: string): string {
  const normalized = normalizeSignatureImageId(imageId);
  if (!normalized) throw new Error('签名图片 ID 无效');
  return `${MANAGED_SIGNATURE_IMAGE_PREFIX}${normalized}`;
}

export function parseManagedSignatureImageId(src: string): string | null {
  const value = String(src || '').trim();
  if (value.toLowerCase().startsWith(MANAGED_SIGNATURE_IMAGE_PREFIX)) {
    return normalizeSignatureImageId(value.slice(MANAGED_SIGNATURE_IMAGE_PREFIX.length));
  }

  try {
    const parsed = new URL(value, 'http://flymail.local');
    const match = parsed.pathname.match(/\/api\/signature-images\/([0-9a-f]{24}\.[0-9a-f]{32})$/i);
    return normalizeSignatureImageId(match?.[1]);
  } catch {
    return null;
  }
}

export function signatureImagePreviewUrl(imageId: string): string {
  const normalized = normalizeSignatureImageId(imageId);
  if (!normalized) return '';
  const basePath = (import.meta.env.BASE_URL || '/').replace(/\/+$/, '');
  return `${basePath || ''}/api/signature-images/${encodeURIComponent(normalized)}`;
}

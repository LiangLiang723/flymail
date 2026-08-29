import type { Attachment } from '../types/mail';

export type AttachmentPreviewKind = 'image' | 'pdf' | 'text' | 'audio' | 'video';

const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif']);
const TEXT_EXTENSIONS = new Set(['txt', 'csv', 'log', 'md', 'json']);
const AUDIO_EXTENSIONS = new Set(['mp3', 'wav', 'ogg', 'm4a', 'aac', 'flac']);
const VIDEO_EXTENSIONS = new Set(['mp4', 'webm', 'mov', 'm4v']);
const GENERIC_MIME_TYPES = new Set(['', 'application/octet-stream', 'binary/octet-stream']);

function fileExtension(filename: string): string {
  const value = String(filename || '').trim().toLowerCase();
  const dot = value.lastIndexOf('.');
  return dot >= 0 ? value.slice(dot + 1) : '';
}

export function getAttachmentPreviewKind(
  attachment: Pick<Attachment, 'content_type' | 'filename'>,
): AttachmentPreviewKind | null {
  const mime = String(attachment.content_type || '').split(';', 1)[0].trim().toLowerCase();

  if (mime === 'application/pdf') return 'pdf';
  if (mime.startsWith('image/') && mime !== 'image/svg+xml') return 'image';
  if (mime.startsWith('audio/')) return 'audio';
  if (mime.startsWith('video/')) return 'video';
  if (mime === 'text/plain' || mime === 'text/csv' || mime === 'application/json') return 'text';

  if (!GENERIC_MIME_TYPES.has(mime)) return null;

  const extension = fileExtension(attachment.filename);
  if (extension === 'pdf') return 'pdf';
  if (IMAGE_EXTENSIONS.has(extension)) return 'image';
  if (TEXT_EXTENSIONS.has(extension)) return 'text';
  if (AUDIO_EXTENSIONS.has(extension)) return 'audio';
  if (VIDEO_EXTENSIONS.has(extension)) return 'video';
  return null;
}

export function safePreviewMime(kind: AttachmentPreviewKind, originalType: string, filename = ''): string {
  if (kind === 'pdf') return 'application/pdf';
  if (kind === 'text') return 'text/plain;charset=utf-8';
  const original = String(originalType || '').split(';', 1)[0].trim().toLowerCase();
  if (kind === 'image' && original.startsWith('image/') && original !== 'image/svg+xml') return original;
  if (kind === 'audio' && original.startsWith('audio/')) return original;
  if (kind === 'video' && original.startsWith('video/')) return original;

  const extension = fileExtension(filename);
  if (kind === 'image') {
    if (extension === 'jpg' || extension === 'jpeg') return 'image/jpeg';
    if (extension === 'png') return 'image/png';
    if (extension === 'gif') return 'image/gif';
    if (extension === 'webp') return 'image/webp';
    if (extension === 'bmp') return 'image/bmp';
    if (extension === 'avif') return 'image/avif';
  }
  if (kind === 'audio') {
    if (extension === 'mp3') return 'audio/mpeg';
    if (extension === 'wav') return 'audio/wav';
    if (extension === 'ogg') return 'audio/ogg';
    if (extension === 'm4a') return 'audio/mp4';
    if (extension === 'aac') return 'audio/aac';
    if (extension === 'flac') return 'audio/flac';
  }
  if (kind === 'video') {
    if (extension === 'mp4' || extension === 'm4v') return 'video/mp4';
    if (extension === 'webm') return 'video/webm';
    if (extension === 'mov') return 'video/quicktime';
  }
  return 'application/octet-stream';
}

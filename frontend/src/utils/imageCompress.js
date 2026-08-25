/**
 * Compresses an image file in the browser before upload - resizes to a
 * max width and re-encodes as JPEG at reduced quality. Runs client-side
 * so the upload itself is small, not just the storage after the fact.
 *
 * Invoice photos from a phone camera are typically 3-5MB; this brings
 * them down to roughly 150-400KB while staying sharp enough to read
 * invoice text (both by the AI and by a human on the review screen).
 */
const MAX_WIDTH = 1600;
const JPEG_QUALITY = 0.75;

export async function compressImage(file) {
  if (!file.type.startsWith('image/')) return file; // e.g. a PDF - pass through untouched

  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, MAX_WIDTH / bitmap.width);
  const width = Math.round(bitmap.width * scale);
  const height = Math.round(bitmap.height * scale);

  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(bitmap, 0, 0, width, height);

  const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', JPEG_QUALITY));
  if (!blob) return file; // compression failed for some reason - fall back to the original

  return new File([blob], (file.name || 'invoice').replace(/\.[^.]+$/, '') + '.jpg', {
    type: 'image/jpeg',
  });
}

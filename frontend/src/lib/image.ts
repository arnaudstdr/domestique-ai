// Utilitaires image côté navigateur (aucune dépendance externe).

/**
 * Redimensionne un fichier image en carré `size`×`size` (recadrage centré
 * « cover ») et l'exporte en JPEG. Corrige l'orientation EXIF quand le
 * navigateur le supporte (`imageOrientation: "from-image"`).
 *
 * Lève une erreur si le fichier n'est pas une image décodable.
 */
export async function resizeImageToSquare(file: File, size = 256): Promise<Blob> {
  const source = await decodeImage(file);

  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;

  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas 2D indisponible.");

  const side = Math.min(source.width, source.height);
  const sx = (source.width - side) / 2;
  const sy = (source.height - side) / 2;
  ctx.drawImage(source, sx, sy, side, side, 0, 0, size, size);

  if (typeof (source as ImageBitmap).close === "function") {
    (source as ImageBitmap).close();
  }

  const blob = await new Promise<Blob | null>((resolve) =>
    canvas.toBlob(resolve, "image/jpeg", 0.85),
  );
  if (!blob) throw new Error("Échec de l'encodage de l'image.");
  return blob;
}

type DecodedImage = HTMLImageElement | ImageBitmap;

async function decodeImage(file: File): Promise<DecodedImage> {
  if (typeof createImageBitmap === "function") {
    try {
      return await createImageBitmap(file, { imageOrientation: "from-image" });
    } catch {
      // Certains navigateurs/SVG ne supportent pas createImageBitmap → repli.
    }
  }
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    await new Promise<void>((resolve, reject) => {
      img.onload = () => resolve();
      img.onerror = () => reject(new Error("Image illisible."));
      img.src = url;
    });
    return img;
  } finally {
    URL.revokeObjectURL(url);
  }
}

/**
 * Convertit un Blob image en data URL base64 (`data:image/…;base64,…`).
 * Utilisé pour l'aperçu local avant/après upload.
 */
export function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("Lecture du fichier impossible."));
    reader.readAsDataURL(blob);
  });
}

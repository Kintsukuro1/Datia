/**
 * Descarga un Blob como archivo. Estaba copiado en cuatro lugares con
 * variantes (con/sin try/finally, con/sin append al DOM): una sola version
 * garantiza que ninguna copia pierda el revoke y filtre el object URL.
 */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  try {
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  } finally {
    URL.revokeObjectURL(url);
  }
}

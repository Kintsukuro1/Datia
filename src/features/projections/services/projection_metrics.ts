/**
 * Comparaciones historicas de una proyeccion.
 *
 * No rellenan nada: si la serie no tiene exactamente los periodos pedidos, o
 * algun valor no es un numero, devuelven `null`. Un cero o una suma parcial
 * presentada como contexto seria una afirmacion que el historico no sostiene.
 */

/** Suma de los ultimos `periods` periodos de `series`, o null si no alcanzan. */
export function sumLastPeriods(
  series: Record<string, any>[],
  periods: number
): number | null {
  if (!Number.isInteger(periods) || periods <= 0 || series.length < periods) {
    return null;
  }
  let total = 0;
  for (const row of series.slice(-periods)) {
    const raw = row?.valor;
    if (raw === null || raw === undefined || raw === '') return null;
    const value = typeof raw === 'number' ? raw : Number(raw);
    if (!Number.isFinite(value)) return null;
    total += value;
  }
  return total;
}

/** Variacion porcentual contra el historico; null si falta un lado o es cero. */
export function variationPct(
  previous: number | null,
  current: number | null
): number | null {
  if (previous === null || current === null || previous === 0) return null;
  return ((current - previous) / Math.abs(previous)) * 100;
}

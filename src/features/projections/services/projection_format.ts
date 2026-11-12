/**
 * Formato comun de Proyecciones. `null` siempre es "—": un cero seria una
 * afirmacion que el backend no hizo.
 */

export const money = (n: number | null | undefined): string =>
  n === null || n === undefined
    ? '—'
    : `$${Math.round(n).toLocaleString('es-CL', { maximumFractionDigits: 0 })}`;

export const pct = (n: number | null | undefined): string =>
  n === null || n === undefined
    ? '—'
    : `${Number(n).toLocaleString('es-CL', { maximumFractionDigits: 1 })}%`;

import type { User } from '../../types';

export type TempPasswordState =
  | Pick<User, 'must_change_password' | 'temp_password_expires_at'>
  | null;

// El backend serializa `datetime.utcnow()` como naive (sin `Z`). `new Date()` lo
// interpreta como hora LOCAL, así que en Chile un vencimiento de 7 días se
// adelantaría 3-4 horas. Si no viene zona, se asume UTC.
const HAS_TIMEZONE = /(?:Z|[+-]\d{2}:?\d{2})$/i;

export const parseServerDate = (value?: string | null): number => {
  if (!value) return NaN;
  const normalized = HAS_TIMEZONE.test(value) ? value : `${value}Z`;
  return Date.parse(normalized);
};

export const isTempPasswordExpired = (
  user: TempPasswordState,
  now: number = Date.now(),
): boolean => {
  if (!user?.temp_password_expires_at) return false;
  const expires = parseServerDate(user.temp_password_expires_at);
  return Number.isFinite(expires) && expires <= now;
};

export const hasPendingPasswordChange = (
  user: TempPasswordState,
  now: number = Date.now(),
): boolean => Boolean(user?.must_change_password) || isTempPasswordExpired(user, now);

export const daysUntilTempPasswordExpiry = (
  user: TempPasswordState,
  now: number = Date.now(),
): number | null => {
  const expires = parseServerDate(user?.temp_password_expires_at);
  if (!Number.isFinite(expires) || expires <= now) return null;
  return Math.max(1, Math.ceil((expires - now) / 86_400_000));
};

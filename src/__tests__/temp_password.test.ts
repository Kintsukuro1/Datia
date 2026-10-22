import { describe, it, expect } from 'vitest';
import {
  daysUntilTempPasswordExpiry,
  hasPendingPasswordChange,
  isTempPasswordExpired,
  parseServerDate,
} from '../features/auth/temp_password';

const NOW = Date.parse('2026-10-19T12:00:00Z');
const DAY_MS = 86_400_000;

describe('parseServerDate: fechas naive del backend son UTC', () => {
  it('sin zona horaria se interpreta como UTC, no como hora local', () => {
    expect(parseServerDate('2026-10-19T12:00:00')).toBe(NOW);
  });

  it('con zona horaria respeta el offset', () => {
    expect(parseServerDate('2026-10-19T09:00:00-03:00')).toBe(NOW);
  });

  it('null o basura devuelve NaN', () => {
    expect(Number.isNaN(parseServerDate(null))).toBe(true);
    expect(Number.isNaN(parseServerDate('no-es-fecha'))).toBe(true);
  });
});

describe('isTempPasswordExpired', () => {
  it('sin fecha de expiración no está vencida', () => {
    expect(isTempPasswordExpired({ temp_password_expires_at: null }, NOW)).toBe(false);
    expect(isTempPasswordExpired(null, NOW)).toBe(false);
  });

  it('vence exactamente en la fecha, no un instante después', () => {
    expect(
      isTempPasswordExpired({ temp_password_expires_at: '2026-10-19T12:00:00' }, NOW),
    ).toBe(true);
    expect(
      isTempPasswordExpired({ temp_password_expires_at: '2026-10-19T12:00:01' }, NOW),
    ).toBe(false);
  });
});

describe('hasPendingPasswordChange', () => {
  it('el flag del admin fuerza el cambio aunque no haya temporal', () => {
    expect(hasPendingPasswordChange({ must_change_password: true }, NOW)).toBe(true);
  });

  it('una temporal vencida fuerza el cambio sin flag', () => {
    expect(
      hasPendingPasswordChange(
        { must_change_password: false, temp_password_expires_at: '2026-10-18T12:00:00' },
        NOW,
      ),
    ).toBe(true);
  });

  it('una temporal vigente no fuerza el cambio', () => {
    expect(
      hasPendingPasswordChange(
        { must_change_password: false, temp_password_expires_at: '2026-10-20T12:00:00' },
        NOW,
      ),
    ).toBe(false);
  });
});

describe('daysUntilTempPasswordExpiry', () => {
  it('redondea hacia arriba y nunca devuelve cero', () => {
    expect(
      daysUntilTempPasswordExpiry(
        { temp_password_expires_at: '2026-10-19T13:00:00' },
        NOW,
      ),
    ).toBe(1);
    expect(
      daysUntilTempPasswordExpiry(
        { temp_password_expires_at: new Date(NOW + 6 * DAY_MS + 1000).toISOString() },
        NOW,
      ),
    ).toBe(7);
  });

  it('vencida o sin fecha devuelve null', () => {
    expect(
      daysUntilTempPasswordExpiry(
        { temp_password_expires_at: '2026-10-18T12:00:00' },
        NOW,
      ),
    ).toBeNull();
    expect(daysUntilTempPasswordExpiry(null, NOW)).toBeNull();
  });
});

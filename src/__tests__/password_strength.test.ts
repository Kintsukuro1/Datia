// Test de `evaluatePassword`, la única lógica nueva del rediseño del cambio de
// clave forzado. Importa del propio componente a propósito: probar la copia
// de la función que corre en pantalla, no una réplica.
//
// La única regla OBLIGATORIA es el mínimo de 6 caracteres (la misma del backend,
// `backend/app/modules/auth/schemas.py`); el resto de los criterios solo suman
// al medidor.
import { describe, it, expect } from 'vitest';
import { evaluatePassword } from '../components/auth/MandatoryPasswordChangeModal';

describe('evaluatePassword: regla obligatoria (mínimo 6)', () => {
  it('menos de 6 caracteres no cumple la regla del backend', () => {
    expect(evaluatePassword('Ab1!').checks.length).toBe(false);
  });

  it('6 caracteres ya cumplen la regla, aunque no tengan composición', () => {
    const r = evaluatePassword('abcdef');
    expect(r.checks.length).toBe(true);
    expect(r.checks.mixedCase).toBe(false);
    expect(r.checks.number).toBe(false);
    expect(r.checks.symbol).toBe(false);
    expect(r.score).toBe(1);
    expect(r.label).toBe('Débil');
  });
});

describe('evaluatePassword: composición (recomendación)', () => {
  it('cuenta mayúsculas+minúsculas, números y símbolos por separado', () => {
    const r = evaluatePassword('abc123');
    expect(r.checks.length).toBe(true);
    expect(r.checks.mixedCase).toBe(false);
    expect(r.checks.number).toBe(true);
    expect(r.checks.symbol).toBe(false);
    expect(r.score).toBe(2);
    expect(r.label).toBe('Media');
  });

  it('sin símbolo pero con caso y número queda en Buena (3/4)', () => {
    const r = evaluatePassword('Clave12');
    expect(r.score).toBe(3);
    expect(r.label).toBe('Buena');
  });

  it('con los cuatro criterios llega a Segura (4/4)', () => {
    const r = evaluatePassword('Clave1!');
    expect(r.checks).toEqual({ length: true, mixedCase: true, number: true, symbol: true });
    expect(r.score).toBe(4);
    expect(r.label).toBe('Segura');
  });

  it('vacía queda en score 0 y Débil', () => {
    const r = evaluatePassword('');
    expect(r.score).toBe(0);
    expect(r.label).toBe('Débil');
  });
});

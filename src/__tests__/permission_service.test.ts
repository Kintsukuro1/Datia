import { describe, it, expect } from 'vitest';
import {
  collectProtectedColumns,
  GovernanceCoverageTable,
  resolveColumnPolicy,
  RoleColumnPermission,
} from '../features/admin/services/permission_service';

// El helper agrupa lo que `GET /permissions/coverage` ya decide por tabla: una
// columna se informa como bloqueada/enmascarada SOLO cuando lo esta para todos
// los roles que pueden leer esa tabla, y una tabla huerfana (sin lectores) no
// informa columnas. Los bugs que estos tests evitan: pintar la lista como si
// aplicara a unos roles y no a otros, o mostrar una fila "aplica a los roles
// con acceso:" con la lista vacia.

const tableRow = (over: Partial<GovernanceCoverageTable>): GovernanceCoverageTable => ({
  table: 't',
  assigned_roles: [],
  visible_to_roles: ['Analista'],
  coverage: 'assigned',
  blocked_columns: [],
  masked_columns: [],
  ...over,
});

describe('collectProtectedColumns: politicas de columna de la cobertura', () => {
  it('agrupa bloqueadas y enmascaradas con los roles que pueden leer la tabla', () => {
    const rows = collectProtectedColumns([
      tableRow({
        table: 'dim_clientes',
        visible_to_roles: ['Analista', 'Finanzas'],
        blocked_columns: ['cuit'],
        masked_columns: ['email', 'telefono'],
      }),
    ]);

    expect(rows).toEqual([
      {
        table: 'dim_clientes',
        readers: ['Analista', 'Finanzas'],
        blocked: ['cuit'],
        masked: ['email', 'telefono'],
      },
    ]);
  });

  it('excluye tablas sin ninguna columna con politica', () => {
    expect(collectProtectedColumns([tableRow({ table: 'fct_ventas' })])).toEqual([]);
  });

  it('una tabla sin lectores no aporta filas aunque el payload traiga columnas', () => {
    // El backend ya manda listas vacias para huerfanas; el helper no depende de
    // esa garantia para no afirmar politicas "para todos los roles" cuando no
    // hay ningun rol.
    const rows = collectProtectedColumns([
      tableRow({
        table: 'huerfana',
        visible_to_roles: [],
        coverage: 'orphaned',
        blocked_columns: ['x'],
      }),
    ]);
    expect(rows).toEqual([]);
  });

  it('conserva el orden del servidor y no mezcla tablas', () => {
    const rows = collectProtectedColumns([
      tableRow({ table: 'a', blocked_columns: ['x'] }),
      tableRow({ table: 'b', masked_columns: ['y'] }),
    ]);

    expect(rows.map((r) => r.table)).toEqual(['a', 'b']);
    expect(rows[0].blocked).toEqual(['x']);
    expect(rows[0].masked).toEqual([]);
    expect(rows[1].masked).toEqual(['y']);
  });
});

// El guard acumula BLOCKED/MASKED por NOMBRE de columna en toda la conexión, sin
// mirar la tabla. Si el panel mirara solo la fila de la tabla elegida, mostraría
// "Permitida" para una columna que el chat bloquea por culpa de otra tabla: un
// no-op silencioso sobre un dato que el admin cree haber abierto.
const policy = (
  table_name: string,
  column_name: string,
  permission_type: RoleColumnPermission['permission_type']
): Pick<RoleColumnPermission, 'table_name' | 'column_name' | 'permission_type'> => ({
  table_name,
  column_name,
  permission_type,
});

describe('resolveColumnPolicy: política efectiva por nombre de columna', () => {
  it('sin fila no hay política y el default es Permitida', () => {
    expect(resolveColumnPolicy([], 'ventas', 'rut')).toEqual({
      rowPolicy: null,
      effectivePolicy: 'ALLOWED',
      enforcedBy: null,
    });
  });

  it('la fila de la tabla elegida manda cuando no hay otra tabla', () => {
    const result = resolveColumnPolicy([policy('ventas', 'rut', 'MASKED')], 'ventas', 'rut');

    expect(result.rowPolicy).toBe('MASKED');
    expect(result.effectivePolicy).toBe('MASKED');
    expect(result.enforcedBy).toBeNull();
  });

  it('otra tabla más restrictiva gana y se informa de dónde viene', () => {
    const result = resolveColumnPolicy(
      [
        policy('ventas', 'rut', 'ALLOWED'),
        policy('clientes', 'rut', 'BLOCKED'),
      ],
      'ventas',
      'rut'
    );

    expect(result.rowPolicy).toBe('ALLOWED');
    expect(result.effectivePolicy).toBe('BLOCKED');
    expect(result.enforcedBy).toBe('clientes');
  });

  it('sin fila propia, una política de otra tabla igual se muestra', () => {
    const result = resolveColumnPolicy(
      [policy('clientes', 'rut', 'MASKED')],
      'ventas',
      'rut'
    );

    expect(result.rowPolicy).toBeNull();
    expect(result.effectivePolicy).toBe('MASKED');
    expect(result.enforcedBy).toBe('clientes');
  });

  it('Bloqueada > Enmascarada sin importar el orden de las filas', () => {
    const policies = [
      policy('a', 'rut', 'MASKED'),
      policy('b', 'rut', 'BLOCKED'),
    ];

    expect(resolveColumnPolicy(policies, 'a', 'rut').effectivePolicy).toBe('BLOCKED');
    expect(resolveColumnPolicy([...policies].reverse(), 'a', 'rut').effectivePolicy).toBe('BLOCKED');
  });

  it('la restricción no cruza columnas distintas', () => {
    const result = resolveColumnPolicy(
      [policy('clientes', 'rut', 'BLOCKED')],
      'ventas',
      'id'
    );

    expect(result.rowPolicy).toBeNull();
    expect(result.effectivePolicy).toBe('ALLOWED');
    expect(result.enforcedBy).toBeNull();
  });

  it('si la fila ya es la más restrictiva, no inventa una advertencia', () => {
    const result = resolveColumnPolicy(
      [
        policy('ventas', 'rut', 'BLOCKED'),
        policy('clientes', 'rut', 'MASKED'),
      ],
      'ventas',
      'rut'
    );

    expect(result.effectivePolicy).toBe('BLOCKED');
    expect(result.enforcedBy).toBeNull();
  });

  it('compara tabla y columna sin distinguir mayúsculas', () => {
    const result = resolveColumnPolicy(
      [
        policy('VENTAS', 'RUT', 'ALLOWED'),
        policy('Clientes', 'Rut', 'BLOCKED'),
      ],
      'ventas',
      'rut'
    );

    expect(result.rowPolicy).toBe('ALLOWED');
    expect(result.enforcedBy).toBe('Clientes');
  });
});

import { describe, it, expect } from 'vitest';
import {
  collectProtectedColumns,
  GovernanceCoverageTable,
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

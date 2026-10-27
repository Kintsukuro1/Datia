import { apiClient } from '../../../shared/api/api_client';

/**
 * Fila de `GET /api/v1/permissions` (`ConnectorDomainService.list_role_table_permissions`).
 * Un dataset recien subido no aparece aca: eso es default-deny, no un fallo.
 */
export interface RoleTablePermission {
  id: number;
  connection_id: number;
  role_id: number;
  role_name: string | null;
  schema_name: string;
  table_name: string;
  is_allowed: boolean;
  granted_by_admin: boolean;
}

/**
 * Fila devuelta dentro de la respuesta de `PUT /api/v1/permissions`. OJO: el
 * router arma estos dicts a mano y NO incluye `role_id`, `connection_id` ni
 * `granted_by_admin` aunque el objeto ORM los tenga. Es lo que el servidor
 * confirmo, asi que es lo unico que esta pantalla toma como verdad.
 */
export interface ConfirmedPermission {
  id: number;
  table_name: string;
  schema_name: string;
  is_allowed: boolean;
}

/** Respuesta de `PUT /api/v1/permissions`. */
export interface SetPermissionsResponse {
  connection_id: number;
  role_id: number;
  is_allowed: boolean;
  permissions: ConfirmedPermission[];
}

/** Politica de columna de un rol en una conexion (CLS editable). */
export type ColumnPermissionType = 'ALLOWED' | 'MASKED' | 'BLOCKED';

/**
 * Fila de `GET /api/v1/permissions/columns`.
 *
 * `ALLOWED` explicito NO es "sin fila": es la excepcion que el seed de CLS
 * respeta en el siguiente arranque. Sin fila, la columna queda legible.
 */
export interface RoleColumnPermission {
  id: number;
  connection_id: number;
  role_id: number;
  role_name: string | null;
  schema_name: string;
  table_name: string;
  column_name: string;
  permission_type: ColumnPermissionType;
}

export interface SetColumnPermissionsResponse {
  connection_id: number;
  role_id: number;
  permission_type: ColumnPermissionType;
  permissions: RoleColumnPermission[];
}

/** Respuesta de `DELETE /api/v1/permissions/columns`. */
export interface DeleteColumnPermissionResponse {
  connection_id: number;
  role_id: number;
  table_name: string;
  column_name: string;
  /** `false` = no había fila. El reset es idempotente, no un 404. */
  deleted: boolean;
}

/**
 * Política que el guardarraíl aplica a una columna, no la que muestra la fila.
 *
 * El chat resuelve BLOCKED/MASKED por NOMBRE de columna en toda la conexión
 * (`dynamic_schema.py` acumula nombres sueltos, sin tabla), así que una política
 * sobre `movimientos.id` también tapa `otra_tabla.id`. Mirar solo la fila de la
 * tabla elegida haría que el selector dijera "Permitida" mientras el chat
 * bloquea la columna: la UI mentiría sobre el efecto real.
 */
export interface EffectiveColumnPolicy {
  /** Política de la fila de la tabla elegida; `null` = sin fila. */
  rowPolicy: ColumnPermissionType | null;
  /** Política que aplica el guard: la más restrictiva para ese nombre. */
  effectivePolicy: ColumnPermissionType;
  /** Otra tabla de la conexión que impone una política más restrictiva. */
  enforcedBy: string | null;
}

const POLICY_RANK: Record<ColumnPermissionType, number> = {
  ALLOWED: 1,
  MASKED: 2,
  BLOCKED: 3,
};

export function resolveColumnPolicy(
  policies: Array<
    Pick<RoleColumnPermission, 'table_name' | 'column_name' | 'permission_type'>
  >,
  table: string,
  column: string
): EffectiveColumnPolicy {
  const columnKey = column.toLowerCase();
  const tableKey = table.toLowerCase();
  const sameColumn = policies.filter(
    (p) => p.column_name.toLowerCase() === columnKey
  );
  const row = sameColumn.find((p) => p.table_name.toLowerCase() === tableKey);
  // sort estable: ante empate gana la primera fila, pero solo importa cuando
  // hay una política MÁS restrictiva que la de la tabla elegida.
  const enforcing = [...sameColumn].sort(
    (a, b) => POLICY_RANK[b.permission_type] - POLICY_RANK[a.permission_type]
  )[0];

  const rowRank = row ? POLICY_RANK[row.permission_type] : POLICY_RANK.ALLOWED;
  const enforcingRank = enforcing ? POLICY_RANK[enforcing.permission_type] : 0;
  const enforcedBy =
    enforcing &&
    enforcing.table_name.toLowerCase() !== tableKey &&
    enforcingRank > rowRank
      ? enforcing.table_name
      : null;

  return {
    rowPolicy: row?.permission_type ?? null,
    effectivePolicy: enforcing?.permission_type ?? 'ALLOWED',
    enforcedBy,
  };
}

/**
 * Cobertura de gobernanza de UNA conexion (`GET /api/v1/permissions/coverage`).
 *
 * Es DERIVADA: el backend no la guarda, la recalcula desde el mismo guardarrail
 * que usa el chat. `coverage: 'orphaned'` es el dato que responde la pregunta que
 * la matriz no puede: que tabla no ve NINGUN rol.
 */
export interface GovernanceCoverageTable {
  table: string;
  assigned_roles: string[];
  visible_to_roles: string[];
  coverage: 'assigned' | 'orphaned';
  blocked_columns: string[];
  masked_columns: string[];
}

export interface GovernanceCoverage {
  connection_id: number;
  connection_name: string;
  tables: GovernanceCoverageTable[];
  summary: {
    total_tables: number;
    assigned_tables: number;
    orphaned_tables: number;
  };
}

/**
 * Columnas con politica de datos (`BLOCKED` / `MASKED`) ya atribuidas a su
 * tabla, tal como las reporta la cobertura.
 *
 * `readers` son los roles que PUEDEN leer la tabla: el backend solo informa una
 * columna como bloqueada o enmascarada cuando lo esta para TODOS ellos, y una
 * tabla sin lectores (huerfana) no informa columnas. Este helper agrupa y filtra
 * ese mismo payload; no decide politicas — el si/no lo sigue diciendo el
 * guardarrail del servidor.
 */
export interface ProtectedColumnsRow {
  table: string;
  readers: string[];
  blocked: string[];
  masked: string[];
}

export function collectProtectedColumns(
  tables: GovernanceCoverageTable[]
): ProtectedColumnsRow[] {
  return tables
    .filter(
      (t) =>
        // Sin lectores no hay a quien aplicarle la politica: la fila diria
        // "aplica a los roles con acceso:" con la lista vacia.
        t.visible_to_roles.length > 0 &&
        (t.blocked_columns.length > 0 || t.masked_columns.length > 0)
    )
    .map((t) => ({
      table: t.table,
      readers: t.visible_to_roles,
      blocked: t.blocked_columns,
      masked: t.masked_columns,
    }));
}

export const permissionService = {
  // Sin catch: si /permissions falla (403 por rol no-admin, 500) el error sube.
  // Degradar a `[]` seria afirmar "no hay ningun permiso en la BD" cuando en
  // realidad no se pudo preguntar, y el admin veria una matriz vacia —que en
  // default-deny se lee igual que "nadie tiene acceso"— cuando no se sabe nada.
  async getPermissions(connectionId?: number): Promise<RoleTablePermission[]> {
    const params: Record<string, any> = {};
    if (connectionId) params.connection_id = connectionId;
    const res = await apiClient.get<RoleTablePermission[]>('/permissions', { params });
    return Array.isArray(res.data) ? res.data : [];
  },

  // El backend define esto con Query params, no con body: `table_names` se
  // repite una vez por tabla. Un body JSON daria 422.
  // Sin catch por la misma razon que arriba: la UI necesita el error para
  // revertir la matriz en vez de pintar casillas concedidas.
  async setPermissions(input: {
    connectionId: number;
    roleId: number;
    tableNames: string[];
    isAllowed: boolean;
  }): Promise<SetPermissionsResponse> {
    const res = await apiClient.put<SetPermissionsResponse>('/permissions', undefined, {
      params: {
        connection_id: input.connectionId,
        role_id: input.roleId,
        table_names: input.tableNames,
        is_allowed: input.isAllowed,
      },
    });
    return res.data;
  },

  // Sin catch, como los dos de arriba: si la cobertura falla, la UI tiene que
  // poder decir "no se pudo calcular" en vez de pintar un resumen de 0 tablas que
  // en default-deny se lee igual que "nadie ve nada".
  async getCoverage(connectionId: number): Promise<GovernanceCoverage> {
    const res = await apiClient.get<GovernanceCoverage>('/permissions/coverage', {
      params: { connection_id: connectionId },
    });
    return res.data;
  },

  // Politicas de columna (CLS). Sin catch por la misma razon que los demas: un
  // fallo tiene que verse, no degradarse a "esta conexion no tiene columnas
  // protegidas", que es una afirmacion distinta y peligrosa.
  async getColumnPermissions(connectionId: number): Promise<RoleColumnPermission[]> {
    const res = await apiClient.get<RoleColumnPermission[]>('/permissions/columns', {
      params: { connection_id: connectionId },
    });
    return Array.isArray(res.data) ? res.data : [];
  },

  // Body, no query: a diferencia de las tablas, una columna es un par
  // (tabla, columna) y en query string obligaria a dos listas paralelas.
  async setColumnPermissions(input: {
    connectionId: number;
    roleId: number;
    permissionType: ColumnPermissionType;
    columns: Array<{ tableName: string; columnName: string }>;
  }): Promise<SetColumnPermissionsResponse> {
    const res = await apiClient.put<SetColumnPermissionsResponse>('/permissions/columns', {
      connection_id: input.connectionId,
      role_id: input.roleId,
      permission_type: input.permissionType,
      columns: input.columns.map((c) => ({
        table_name: c.tableName,
        column_name: c.columnName,
      })),
    });
    return res.data;
  },

  // Query params, no body: DELETE no lleva cuerpo en este contrato. Es
  // idempotente por diseño (`deleted: false` si no había fila), así que la UI
  // puede pedir el reset dos veces sin convertir el no-op en un error.
  async deleteColumnPermission(input: {
    connectionId: number;
    roleId: number;
    tableName: string;
    columnName: string;
  }): Promise<DeleteColumnPermissionResponse> {
    const res = await apiClient.delete<DeleteColumnPermissionResponse>(
      '/permissions/columns',
      {
        params: {
          connection_id: input.connectionId,
          role_id: input.roleId,
          table_name: input.tableName,
          column_name: input.columnName,
        },
      }
    );
    return res.data;
  },
};
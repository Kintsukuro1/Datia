from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.modules.admin_catalog.models import CorporateConnection
from app.modules.catalog.services.schema_inspector import (
    SchemaInspector,
    quote_sql_identifier as quote_ident,
)

def es_celda_nula(v: Any) -> bool:
    """Celda sin dato: SOLO `None` (NULL de SQL).

    Los textos 'none'/'null' NO cuentan como nulo: son contenido real (un
    proveedor, un estado, una observacion) y tratarlos como hueco borraba
    filas vivas. El audit y `apply_null_policy` ya usan `IS NULL`; esto alinea
    la remediacion in-memory del chat y sus avisos con esa misma definicion.
    """
    return v is None

class NullManagerService:
    """
    Manages detection, audit, and remediation strategies for NULL values
    in PostgreSQL corporate connections.

    Strategies:
      - 'delete_rows': Removes rows that contain NULLs in the affected columns.
      - 'mode': Imputes NULLs with the most frequent non-null value of the column.
      - 'nearest': Imputes NULLs with the nearest preceding/succeeding valid value (forward-fill).
      - 'open': Preserves NULLs and delegates interactive remediation to the end-user in Chat.

    SCOPE OF "NULL" (changed): a cell is treated as null ONLY when it is SQL
    NULL (`IS NULL`). The strings 'null' and 'none' are NO LONGER treated as
    null. They used to be, which meant `delete_rows` erased live rows whose
    real content was the text 'None' (a proveedor, an estado, a observacion).
    The user asked to delete nulls, not rows that happen to spell 'None'. If
    that cleanup is ever wanted it must be a separate, explicit opt-in policy.
    """

    @classmethod
    def audit_connection_nulls(cls, conn: CorporateConnection, db: Optional[Session] = None) -> Dict[str, Any]:
        tables_meta = SchemaInspector.introspect_connection_metadata(conn)
        tables_with_nulls: List[Dict[str, Any]] = []

        # `connector_engine` y no `build_engine_for_connector`: libera el pool al
        # salir de la auditoria. Crear el engine a mano dejaba los sockets del
        # pool sin cerrar en cada request (mismo patron que schema_inspector).
        from app.core.database import connector_engine
        with connector_engine(conn) as eng, eng.connect() as connection:
            for tbl_info in tables_meta:
                tbl_name = tbl_info["table_name"]
                schema_name = tbl_info.get("schema_name", "public")
                total_rows = tbl_info.get("row_count", 0)
                if total_rows == 0:
                    continue

                null_cols = []
                unverifiable = []
                for col in tbl_info.get("columns", []):
                    col_name = col["name"]
                    try:
                        res = connection.execute(
                            text(
                                f'SELECT COUNT(*) FROM {quote_ident(schema_name)}.{quote_ident(tbl_name)} '
                                f'WHERE {quote_ident(col_name)} IS NULL'
                            )
                        ).scalar()
                        null_count = int(res or 0)
                        if null_count > 0:
                            null_cols.append({
                                "column_name": col_name,
                                "data_type": col.get("data_type", "TEXT"),
                                "null_count": null_count,
                                "null_percentage": round((null_count / total_rows) * 100, 1)
                            })
                    except Exception as ex:
                        # GRANT de tabla pero no de columna: el COUNT lanza.
                        # Omitirla en silencio hacia `has_nulls: False` ->
                        # "Base de datos limpia" (afirmacion falsa) y, peor,
                        # `delete_rows` borraba filas usando un conteo
                        # incompleto. Se reporta como no verificable.
                        unverifiable.append({
                            "column_name": col_name,
                            "reason": str(ex)
                        })

                if null_cols or unverifiable:
                    tables_with_nulls.append({
                        "table_name": tbl_name,
                        "schema_name": schema_name,
                        "total_rows": total_rows,
                        "columns_with_nulls": null_cols,
                        "total_null_columns": len(null_cols),
                        "unverifiable_columns": unverifiable
                    })

        total_unverifiable = sum(len(t["unverifiable_columns"]) for t in tables_with_nulls)
        return {
            "has_nulls": any(t["total_null_columns"] > 0 for t in tables_with_nulls),
            "total_tables_with_nulls": len(tables_with_nulls),
            # `has_nulls` solo mira columnas CON nulos. Las no verificables se
            # informan aparte para que "no detectamos nulos" no se lea como
            # "esta base esta limpia" cuando en realidad no se pudo mirar.
            "total_unverifiable_columns": total_unverifiable,
            "is_complete": total_unverifiable == 0,
            "tables": tables_with_nulls,
            "current_policy": getattr(conn, "null_policy", "open") or "open"
        }

    @classmethod
    def apply_null_policy(
        cls, conn: CorporateConnection, policy: str, db: Session, confirm: bool = False
    ) -> Dict[str, Any]:
        """Remediacion FISICA de la base corporativa (DELETE/UPDATE + commit).

        Reservada al endpoint administrativo
        `/catalog/connections/{id}/nulls-policy`, que exige `get_current_admin`.
        El chat NO usa este metodo: aplica `apply_in_memory_remediation` sobre
        las filas de la respuesta y deja la base intacta (plan 002).

        `confirm=True` es obligatorio para las politicas que escriben. El clic
        en la UI no viaja al backend: sin este flag, cualquier cliente con
        token de admin disparaba DELETE/UPDATE en un solo request.
        """
        if policy not in ("delete_rows", "mode", "nearest", "open"):
            raise ValueError(f"Política de nulos no soportada: {policy}")

        if policy != "open" and not confirm:
            raise ValueError(
                f"La política '{policy}' modifica la base corporativa (DELETE/UPDATE). "
                "Reenvíe la petición con confirm=true para ejecutarla."
            )

        # Un SOLO commit, al final. Persistir la politica al principio dejaba la
        # conexion diciendo "política activa" cuando el audit o la remediacion
        # fallaban, y el frontend la mostraba como aplicada sobre una base sin
        # remediar. Si algo revienta, rollback: la politica no se guarda.
        conn.null_policy = policy

        if policy == "open":
            db.commit()
            return {
                "status": "success",
                "policy": "open",
                "rows_affected": 0,
                "message": "Política configurada: La decisión se delega proactivamente al usuario en el chat."
            }

        try:
            audit = cls.audit_connection_nulls(conn, db)
        except Exception:
            # La politica NO se persiste si ni siquiera pudimos mirar la base.
            db.rollback()
            raise

        if not audit["has_nulls"]:
            db.commit()
            msg = "No se detectaron valores nulos: no había nada que remediar."
            if not audit.get("is_complete", True):
                # No afirmar "limpia": hubo columnas que no se pudieron leer.
                msg = (
                    f"No se detectaron nulos en las {audit['total_unverifiable_columns']} "
                    "column(s) que sí se pudieron verificar, pero el escaneo fue INCOMPLETO: "
                    "revise los permisos de columna antes de tomar esto como una base limpia."
                )
            return {
                "status": "no_op",
                "policy": policy,
                "rows_affected": 0,
                "message": msg,
            }

        rows_affected = 0

        try:
            from app.core.database import connector_engine
            with connector_engine(conn) as eng:
                with eng.connect() as connection:
                    for tbl in audit["tables"]:
                        tbl_name = tbl["table_name"]
                        schema_name = tbl.get("schema_name", "public")
                        cols = tbl["columns_with_nulls"]
                        if not cols:
                            continue

                        quoted_schema = quote_ident(schema_name)
                        quoted_table = quote_ident(tbl_name)
                        if policy == "delete_rows":
                            conds = " OR ".join(
                                f'{quote_ident(c["column_name"])} IS NULL' for c in cols
                            )
                            res = connection.execute(
                                text(f'DELETE FROM {quoted_schema}.{quoted_table} WHERE {conds}')
                            )
                            rows_affected += max(res.rowcount or 0, 0)

                        elif policy == "mode":
                            for c in cols:
                                quoted_col = quote_ident(c["column_name"])
                                mode_row = connection.execute(
                                    text(
                                        f'SELECT {quoted_col} FROM {quoted_schema}.{quoted_table} '
                                        f'WHERE {quoted_col} IS NOT NULL GROUP BY {quoted_col} '
                                        # Desempate explicito: con dos valores igual de
                                        # frecuentes, `COUNT(*) DESC` solo no define
                                        # ganador y el planner elegia distinto entre
                                        # corridas. Gana el menor.
                                        f'ORDER BY COUNT(*) DESC, {quoted_col} ASC LIMIT 1'
                                    )
                                ).fetchone()
                                if mode_row and mode_row[0] is not None:
                                    res = connection.execute(
                                        text(
                                            f'UPDATE {quoted_schema}.{quoted_table} '
                                            f'SET {quoted_col} = :m_val WHERE {quoted_col} IS NULL'
                                        ),
                                        {"m_val": mode_row[0]}
                                    )
                                    rows_affected += max(res.rowcount or 0, 0)

                        elif policy == "nearest":
                            for c in cols:
                                quoted_col = quote_ident(c["column_name"])
                                # Forward-fill REAL: LAG() sobre ctid. Antes se
                                # hacia `LIMIT 1` sin ORDER BY, que devuelve una
                                # fila arbitraria: con salario=[100,NULL,500]
                                # rellenaba 100 en vez del precedente 500.
                                res = connection.execute(text(
                                    f'WITH ordered AS ('
                                    f'  SELECT ctid AS rid, {quoted_col} AS v,'
                                    f'         LAG({quoted_col}) OVER (ORDER BY ctid) AS prev_v'
                                    f'  FROM {quoted_schema}.{quoted_table}'
                                    f'), filled AS ('
                                    f'  SELECT rid, COALESCE(v, prev_v) AS nv FROM ordered'
                                    f') '
                                    f'UPDATE {quoted_schema}.{quoted_table} t SET {quoted_col} = f.nv '
                                    f'FROM filled f '
                                    f'WHERE t.ctid = f.rid AND t.{quoted_col} IS NULL AND f.nv IS NOT NULL'
                                ))
                                rows_affected += max(res.rowcount or 0, 0)
                                # Los nulos iniciales (sin predecessor) toman el
                                # primer valor no nulo. El EXISTS evita el
                                # UPDATE ... SET col = NULL: sin el, rowcount
                                # contaria filas "afectadas" que en realidad no
                                # cambiaron de valor.
                                res = connection.execute(text(
                                    f'UPDATE {quoted_schema}.{quoted_table} SET {quoted_col} = ('
                                    f'  SELECT {quoted_col} FROM {quoted_schema}.{quoted_table}'
                                    f'  WHERE {quoted_col} IS NOT NULL ORDER BY ctid LIMIT 1)'
                                    f' WHERE {quoted_col} IS NULL'
                                    f' AND EXISTS (SELECT 1 FROM {quoted_schema}.{quoted_table} WHERE {quoted_col} IS NOT NULL)'
                                ))
                                rows_affected += max(res.rowcount or 0, 0)
                    connection.commit()
        except Exception:
            # Si la remediacion falla, la politica tampoco queda persistida: la
            # base quedo sin remediar y decir lo contrario es peor que no decir nada.
            db.rollback()
            raise

        db.commit()

        if rows_affected == 0:
            # Decir "aplicada con éxito" cuando no se toco ni una celda es la
            # peor version de este bug: el usuario cree que la base quedo
            # remediada. Ocurria con columnas 100% nulas bajo 'mode' (no hay
            # moda que calcular) y cuando no se resolvio ningun target_path.
            return {
                "status": "no_op",
                "policy": policy,
                "rows_affected": 0,
                "message": (
                    f"No se modificó ninguna celda bajo la política '{policy}'. "
                    "El escaneo detectó nulos pero no había valores válidos para imputar "
                    "(o no se pudo abrir la base de destino). No se remedió nada."
                ),
            }

        return {
            "status": "success",
            "policy": policy,
            "rows_affected": rows_affected,
            "message": f"Remediación de nulos aplicada bajo la política '{policy}': {rows_affected} celda(s) modificada(s)."
        }

    @classmethod
    def apply_in_memory_remediation(
        cls,
        rows: List[Dict[str, Any]],
        columns: List[str],
        policy: str
    ) -> List[Dict[str, Any]]:
        """
        Applies in-memory null remediation to a list of dict rows for interactive user remediation.
        """
        null_cols = set()
        for r in rows:
            for c in columns:
                v = r.get(c)
                if es_celda_nula(v):
                    null_cols.add(c)

        if not null_cols:
            return rows

        if policy == "delete_rows":
            return [
                r for r in rows
                if not any(es_celda_nula(r.get(c)) for c in null_cols)
            ]

        if policy == "mode":
            new_rows = [dict(r) for r in rows]
            for col in null_cols:
                valid_vals = [
                    r[col] for r in new_rows
                    if not es_celda_nula(r.get(col))
                ]
                if valid_vals:
                    # Desempate determinista: entre dos valores con la misma
                    # frecuencia gana el menor, igual que `ORDER BY ..., col ASC`
                    # del camino fisico. Antes decidia el orden interno del set.
                    mode_val = max(sorted(set(valid_vals)), key=valid_vals.count)
                    for r in new_rows:
                        if es_celda_nula(r.get(col)):
                            r[col] = mode_val
            return new_rows

        if policy == "nearest":
            new_rows = [dict(r) for r in rows]
            for col in null_cols:
                last_val = None
                for r in new_rows:
                    if not es_celda_nula(r.get(col)):
                        last_val = r[col]
                    elif last_val is not None:
                        r[col] = last_val
                first_val = next(
                    (r[col] for r in new_rows if not es_celda_nula(r.get(col))),
                    None
                )
                if first_val is not None:
                    for r in new_rows:
                        if es_celda_nula(r.get(col)):
                            r[col] = first_val
            return new_rows

        return rows

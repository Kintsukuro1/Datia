from typing import List, Optional, Any, Dict, Set, Tuple
import re
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.constants import IGNORED_METADATA_TABLES
from app.modules.admin_catalog.models import CorporateConnection


def quote_sql_identifier(name: str) -> str:
    """Cita un identificador para SQL textual y escapa comillas dobles.

    Los nombres vienen de la base del cliente, no de una lista controlada: un
    identificador como `a"b` rompia el SQL o permitia inyectar sentencias en los
    caminos que arman consultas con f-strings. PostgreSQL y SQLite comparten la
    regla de duplicar la comilla dentro de comillas dobles.
    """
    return '"' + str(name).replace('"', '""') + '"'


class SchemaIntrospectionError(RuntimeError):
    """Fallo al CONECTAR con la base del cliente (no al leer una tabla).

    Antes `introspect_connection_metadata` tragaba este error y devolvia `[]`,
    que es indistinguible de "esta base no tiene tablas": un password mal
    descifrado, un Postgres caido o un schema sin USAGE producian un
    DataDictionaryResponse(total_tables=0) con HTTP 200 y el frontend
    mostrandoles "esta base no tiene tablas".
    """


class SchemaInspector:
    """
    Encapsulates physical database introspection (PostgreSQL)
    and connection resolution.
    """

    IGNORED_TABLES = IGNORED_METADATA_TABLES

    @classmethod
    def default_connection(cls, db: Session) -> Optional[CorporateConnection]:
        """La conexion que se usa cuando el request no pidio una: activa, o la ultima.

        Devuelve None si no hay ninguna registrada. No lanza a proposito: los
        callers que pueden degradar (un prompt, una etiqueta de auditoria) lo
        usan como "no hay base", y `resolve_connection` lo convierte en error.
        """
        conn = db.query(CorporateConnection).filter(CorporateConnection.is_active == True).order_by(CorporateConnection.id.desc()).first()
        if not conn:
            conn = db.query(CorporateConnection).order_by(CorporateConnection.id.desc()).first()
        return conn

    @classmethod
    def active_connection(cls, db: Session) -> Optional[CorporateConnection]:
        """La conexión ENCENDIDA, o None. No cae a la última inactiva.

        `default_connection` conserva el fallback histórico para metadata, pero
        una consulta de datos sin id explícito no puede terminar en una base
        apagada por el simple hecho de ser la última fila.
        """
        return (
            db.query(CorporateConnection)
            .filter(CorporateConnection.is_active == True)
            .order_by(CorporateConnection.id.desc())
            .first()
        )

    @classmethod
    def resolve_connection(cls, db: Session, connection_id: Optional[int]) -> CorporateConnection:
        """
        Resolves the CorporateConnection that the request targets.

        Un `connection_id` explicito e inexistente es ERROR, no fallback. Antes
        caia en silencio a "la conexion no-uploadada" y el caller terminaba
        introspeccionando OTRA base creyendo que era la que pidio (mismo criterio
        que `CatalogDomainService.create_catalog_item`). Sin `connection_id` el
        camino sigue siendo el historico: conexion activa, o la ultima existente.
        """
        conn = None
        if connection_id:
            conn = db.query(CorporateConnection).filter(CorporateConnection.id == connection_id).first()
            if not conn:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Conexión corporativa {connection_id} no encontrada."
                )

        if not conn:
            conn = cls.default_connection(db)

        if not conn:
            # Sin conexiones no hay base que introspeccionar. Antes se fabricaba
            # una conexion SQLite falsa sobre la demo interna y el diccionario
            # respondia las tablas de OTRA base como si fueran del cliente.
            raise SchemaIntrospectionError(
                "No hay ninguna conexión corporativa registrada. Registra una en Administración, pestaña Conexiones."
            )
        return conn

    @staticmethod
    def _short_value(value: Any, limit: int = 80) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text[:limit] if text else None

    @staticmethod
    def _infer_pattern(values: List[str]) -> Optional[str]:
        """Patron textual grueso, solo como evidencia para el LLM.

        No es un tipo de dato oficial: es una pista (8 digitos, ISO, 2 letras)
        que el administrador confirma en la revision. No se guarda como verdad.
        """
        vals = [str(v).strip() for v in values if v is not None and str(v).strip()]
        if not vals:
            return None
        if all(re.fullmatch(r"\d{8}", v) for v in vals):
            return "8 digitos (posible DDMMYYYY)"
        if all(re.fullmatch(r"\d{7,8}", v) for v in vals):
            return "7-8 digitos (posible DDMMYYYY)"
        if all(re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?", v) for v in vals):
            return "fecha-hora ISO YYYY-MM-DD HH:MM:SS"
        if all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) for v in vals):
            return "fecha ISO YYYY-MM-DD"
        if all(re.fullmatch(r"\d{2}/\d{2}/\d{4}", v) for v in vals):
            return "fecha DD/MM/YYYY"
        if all(re.fullmatch(r"\d{4}/\d{2}/\d{2}", v) for v in vals):
            return "fecha YYYY/MM/DD"
        if all(re.fullmatch(r"[A-Za-z]{2}", v) for v in vals):
            return "codigo de 2 letras (posible ISO-3166 de pais)"
        if all(re.fullmatch(r"[A-Za-z0-9._/-]+", v) for v in vals):
            return "alfanumerico"
        return None

    @classmethod
    def profile_column(
        cls,
        connection,
        table_ref: str,
        col_name: str,
        col_type: str,
        row_count: int,
        include_values: bool,
    ) -> Dict[str, Any]:
        """Perfil de una columna calculado sobre TODAS las filas.

        `include_values=False` es la politica segura para columnas con
        MASKED/BLOCKED: se devuelven conteos, cardinalidad y largos, pero nunca
        minimos, maximos, top values ni muestras. La descripcion que escriba el
        LLM se guarda y la ven todos los roles; un valor crudo ahi seria una
        fuga de CLS.
        """
        from sqlalchemy import text

        col_id = quote_sql_identifier(col_name)
        type_upper = (col_type or "").upper()
        numeric = any(
            token in type_upper
            for token in ("INT", "NUMERIC", "DECIMAL", "REAL", "DOUBLE", "FLOAT", "MONEY", "SERIAL")
        )

        select_parts = [
            f"COUNT({col_id}) AS non_null",
            f"COUNT(DISTINCT {col_id}) AS distinct_count",
            f"MIN({col_id}) AS min_v",
            f"MAX({col_id}) AS max_v",
        ]
        if numeric:
            select_parts.append(f"AVG({col_id}) AS avg_v")
        else:
            select_parts.extend([
                f"MIN(LENGTH(CAST({col_id} AS TEXT))) AS len_min",
                f"MAX(LENGTH(CAST({col_id} AS TEXT))) AS len_max",
            ])

        try:
            stats = connection.execute(
                text(f"SELECT {', '.join(select_parts)} FROM {table_ref}")
            ).mappings().one()
        except Exception as ex:
            return {"tipo": col_type, "error": f"no se pudo perfilar: {str(ex)[:120]}"}

        non_null = int(stats["non_null"] or 0)
        distinct = int(stats["distinct_count"] or 0)
        nulls = max(int(row_count or 0) - non_null, 0)
        profile: Dict[str, Any] = {
            "tipo": col_type,
            "nulos": nulls,
            "nulos_pct": round((nulls / row_count) * 100, 1) if row_count else 0.0,
            "distintos": distinct,
        }

        if include_values:
            profile["min"] = cls._short_value(stats["min_v"])
            profile["max"] = cls._short_value(stats["max_v"])
            if numeric and stats["avg_v"] is not None:
                profile["promedio"] = round(float(stats["avg_v"]), 2)
            if not numeric:
                profile["largo_min"] = int(stats["len_min"]) if stats["len_min"] is not None else None
                profile["largo_max"] = int(stats["len_max"]) if stats["len_max"] is not None else None
        else:
            profile["valores_omitidos"] = True
            if not numeric:
                profile["largo_min"] = int(stats["len_min"]) if stats["len_min"] is not None else None
                profile["largo_max"] = int(stats["len_max"]) if stats["len_max"] is not None else None

        if include_values and distinct > 0:
            limit = 20 if distinct <= 20 else 10
            try:
                top_rows = connection.execute(
                    text(
                        f"SELECT {col_id} AS value, COUNT(*) AS n FROM {table_ref} "
                        f"WHERE {col_id} IS NOT NULL GROUP BY {col_id} "
                        f"ORDER BY n DESC LIMIT {limit}"
                    )
                ).fetchall()
                profile["top"] = [
                    {"valor": cls._short_value(r[0], 80), "n": int(r[1])}
                    for r in top_rows
                    if cls._short_value(r[0], 80) is not None
                ]
            except Exception:
                profile["top"] = []

            try:
                # Muestra representativa. En tablas grandes se limita a un 1%
                # por TABLESAMPLE para no escanear millones de filas; si el
                # sample no devuelve nada, se cae a las primeras filas.
                if int(row_count or 0) <= 50_000:
                    sample_rows = connection.execute(
                        text(
                            f"SELECT {col_id} FROM {table_ref} "
                            f"WHERE {col_id} IS NOT NULL ORDER BY random() LIMIT 5"
                        )
                    ).fetchall()
                else:
                    sample_rows = connection.execute(
                        text(
                            f"SELECT {col_id} FROM {table_ref} TABLESAMPLE SYSTEM (1) "
                            f"WHERE {col_id} IS NOT NULL LIMIT 5"
                        )
                    ).fetchall()
                    if not sample_rows:
                        sample_rows = connection.execute(
                            text(
                                f"SELECT {col_id} FROM {table_ref} "
                                f"WHERE {col_id} IS NOT NULL LIMIT 5"
                            )
                        ).fetchall()
                profile["muestras"] = [
                    cls._short_value(r[0], 80)
                    for r in sample_rows
                    if cls._short_value(r[0], 80) is not None
                ]
            except Exception:
                profile["muestras"] = []

            pattern_values = [v["valor"] for v in profile.get("top", [])]
            pattern_values += profile.get("muestras", [])
            profile["patron"] = cls._infer_pattern(pattern_values)

        return profile

    @classmethod
    def introspect_connection_metadata(
        cls,
        conn_obj: CorporateConnection,
        include_profile: bool = False,
        sensitive_columns: Optional[Set[Tuple[str, str]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Introspects tables and columns with data types, nullability, primary keys, and sample values
        for a PostgreSQL corporate connection.

        `include_profile=True` agrega un perfil de cada columna calculado sobre
        todas las filas (nulos, distintos, min/max, top values, muestras y
        patron). Es la evidencia que usa el enricher con IA. `sensitive_columns`
        son pares (tabla, columna) con politica MASKED/BLOCKED: para esas
        columnas no se devuelven valores crudos, solo conteos y largos.
        """
        from sqlalchemy import inspect as sa_inspect, text
        from app.core.database import connector_engine

        sensitive = {
            (str(table).lower(), str(column).lower())
            for table, column in (sensitive_columns or set())
        }
        tables_metadata: List[Dict[str, Any]] = []

        try:
            # `connector_engine` y no `build_engine_for_connector`: este ultimo
            # devuelve el engine crudo y el pool de sockets (5+10) queda
            # huerfano cuando se sale del bloque. Cada peticion del diccionario
            # abria uno. Ver el docstring de `connector_engine`.
            with connector_engine(conn_obj) as eng:
                inspector = sa_inspect(eng)
                all_schemas = [s for s in inspector.get_schema_names() if s not in ("information_schema", "pg_catalog", "pg_toast")]
                target_schema = "public" if "public" in all_schemas else (all_schemas[0] if all_schemas else None)

                table_names = inspector.get_table_names(schema=target_schema)
                active_tables = [t for t in table_names if t.lower() not in cls.IGNORED_TABLES]

                if conn_obj:
                    from app.core.database import SessionLocal
                    from app.modules.admin_catalog.models import RoleTablePermission, CorporateConnection
                    _s = SessionLocal()
                    try:
                        if conn_obj.is_uploaded:
                            assigned = _s.query(RoleTablePermission.table_name).filter(RoleTablePermission.connection_id == conn_obj.id).all()
                            if assigned:
                                assigned_set = {r[0].lower() for r in assigned if r[0]}
                                active_tables = [t for t in active_tables if t.lower() in assigned_set]
                        else:
                            uploaded_conn_ids = [c[0] for c in _s.query(CorporateConnection.id).filter(CorporateConnection.is_uploaded == True).all()]
                            if uploaded_conn_ids:
                                uploaded_tables = _s.query(RoleTablePermission.table_name).filter(RoleTablePermission.connection_id.in_(uploaded_conn_ids)).all()
                                uploaded_set = {r[0].lower() for r in uploaded_tables if r[0]}
                                active_tables = [t for t in active_tables if t.lower() not in uploaded_set]
                    finally:
                        _s.close()

                with eng.connect() as connection:
                    for tbl in active_tables:
                        try:
                            schema_prefix = f'{quote_sql_identifier(target_schema)}.' if target_schema else ""
                            cnt_res = connection.execute(
                                text(f'SELECT COUNT(*) FROM {schema_prefix}{quote_sql_identifier(tbl)}')
                            ).scalar()
                            row_count = int(cnt_res or 0)
                        except Exception:
                            row_count = 0

                        cols_info = inspector.get_columns(tbl, schema=target_schema)
                        pk_info = inspector.get_pk_constraint(tbl, schema=target_schema)
                        pk_cols = set(pk_info.get("constrained_columns", [])) if pk_info else set()

                        cols_data = []
                        for col in cols_info:
                            col_name = col["name"]
                            col_type = str(col["type"])
                            is_pk = col_name in pk_cols
                            is_null = col.get("nullable", True)
                            def_val = str(col.get("default", "")) if col.get("default") is not None else None

                            key = (str(tbl).lower(), str(col_name).lower())
                            is_sensitive = key in sensitive
                            table_ref = f"{schema_prefix}{quote_sql_identifier(tbl)}"
                            profile = None

                            if include_profile:
                                profile = cls.profile_column(
                                    connection,
                                    table_ref,
                                    col_name,
                                    col_type,
                                    row_count,
                                    include_values=not is_sensitive,
                                )
                                # El heuristico usa 3 valores; se reutilizan los
                                # top del perfil para no pagar otra query. En
                                # columnas sensibles no se le pasa ningun valor.
                                sample_vals = (
                                    []
                                    if is_sensitive
                                    else [
                                        item["valor"]
                                        for item in (profile.get("top") or [])[:3]
                                        if item.get("valor") is not None
                                    ]
                                )
                            else:
                                sample_vals = []
                                if not is_sensitive:
                                    try:
                                        quoted_col = quote_sql_identifier(col_name)
                                        samples_res = connection.execute(
                                            text(
                                                f'SELECT DISTINCT {quoted_col} FROM {table_ref} '
                                                f'WHERE {quoted_col} IS NOT NULL LIMIT 3'
                                            )
                                        ).fetchall()
                                        sample_vals = [str(r[0]) for r in samples_res if r[0] is not None]
                                    except Exception:
                                        pass

                            column_meta = {
                                "name": col_name,
                                "data_type": col_type,
                                "is_pk": is_pk,
                                "is_nullable": is_null,
                                "default_value": def_val,
                                "sample_values": sample_vals,
                            }
                            if profile is not None:
                                column_meta["profile"] = profile
                            cols_data.append(column_meta)

                        table_meta = {
                            "table_name": tbl,
                            "schema_name": target_schema or "public",
                            "row_count": row_count,
                            "columns": cols_data,
                        }
                        if include_profile:
                            try:
                                fks = []
                                for fk in inspector.get_foreign_keys(tbl, schema=target_schema):
                                    fks.append({
                                        "columns": fk.get("constrained_columns") or [],
                                        "referred_table": fk.get("referred_table"),
                                        "referred_columns": fk.get("referred_columns") or [],
                                    })
                                table_meta["foreign_keys"] = fks
                            except Exception:
                                table_meta["foreign_keys"] = []
                        tables_metadata.append(table_meta)
        except SchemaIntrospectionError:
            raise
        except Exception as ex:
            # No se devuelve `[]`: el caller no puede distinguir "no se pudo
            # conectar" de "la base no tiene tablas" y respondia 200 con
            # total_tables=0, diciendoselo al usuario como un hecho.
            raise SchemaIntrospectionError(
                f"No se pudo introspeccionar la conexión {getattr(conn_obj, 'name', conn_obj)}: {ex}"
            ) from ex

        return tables_metadata

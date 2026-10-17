from typing import List, Optional, Any, Dict
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.modules.admin_catalog.models import CorporateConnection


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

    IGNORED_TABLES = {
        "roles", "domains", "corporate_connections",
        "users", "role_domain_links", "role_table_permissions",
        "role_column_permissions", "semantic_catalog", "audit_logs",
        "user_sessions", "alembic_version"
    }

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
            conn = db.query(CorporateConnection).filter(CorporateConnection.is_active == True).order_by(CorporateConnection.id.desc()).first()
            if not conn:
                conn = db.query(CorporateConnection).order_by(CorporateConnection.id.desc()).first()

        if not conn:
            # Sin conexiones no hay base que introspeccionar. Antes se fabricaba
            # una conexion SQLite falsa sobre la demo interna y el diccionario
            # respondia las tablas de OTRA base como si fueran del cliente.
            raise SchemaIntrospectionError(
                "No hay ninguna conexión corporativa registrada. Registra una en Administración, pestaña Conexiones."
            )
        return conn

    @classmethod
    def introspect_connection_metadata(cls, conn_obj: CorporateConnection) -> List[Dict[str, Any]]:
        """
        Introspects tables and columns with data types, nullability, primary keys, and sample values
        for a PostgreSQL corporate connection.
        """
        from sqlalchemy import inspect as sa_inspect, text
        from app.core.database import connector_engine

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
                            schema_prefix = f'"{target_schema}".' if target_schema else ""
                            cnt_res = connection.execute(text(f'SELECT COUNT(*) FROM {schema_prefix}"{tbl}"')).scalar()
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

                            sample_vals = []
                            try:
                                samples_res = connection.execute(
                                    text(f'SELECT DISTINCT "{col_name}" FROM {schema_prefix}"{tbl}" WHERE "{col_name}" IS NOT NULL LIMIT 3')
                                ).fetchall()
                                sample_vals = [str(r[0]) for r in samples_res if r[0] is not None]
                            except Exception:
                                pass

                            cols_data.append({
                                "name": col_name,
                                "data_type": col_type,
                                "is_pk": is_pk,
                                "is_nullable": is_null,
                                "default_value": def_val,
                                "sample_values": sample_vals
                            })

                        tables_metadata.append({
                            "table_name": tbl,
                            "schema_name": target_schema or "public",
                            "row_count": row_count,
                            "columns": cols_data
                        })
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

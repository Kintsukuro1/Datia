import json
import os
import re
import secrets
import shutil
import time
import uuid
from typing import List, Optional, Any, Dict
from fastapi import HTTPException, status, UploadFile
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import ADMIN_ROLES
from app.core.logging import logger
from app.core.security import encrypt_credential
from app.modules.admin_catalog.models import (
    ColumnPermissionType, CorporateConnection, DatabaseType, SemanticCatalog,
    RoleTablePermission, RoleColumnPermission,
)
from app.modules.auth.models import Role
from app.modules.admin_catalog.schemas import (
    CorporateConnectionCreate, CorporateConnectionUpdate, CorporateConnectionOut,
    ConnectionTestRequest, ConnectionTestResult
)
from app.modules.admin_catalog.tabular_importer import (
    convert_uploaded_file_to_postgres,
)
from app.modules.catalog.services.catalog_service import CatalogDomainService
from app.modules.catalog.services.schema_inspector import quote_sql_identifier
from app.modules.system.health_service import HealthService

class ConnectorDomainService:
    """
    Domain service for Corporate Connection lifecycle, file uploads, and connectivity health checks.
    """

    # Prefijo de los roles dedicados que se crean para cada dataset subido. Se usa
    # en `delete_connector` para no tocar jamas un rol que este servicio no creo
    # (p. ej. el usuario de provision en un conector subido antes de este cambio).
    DATASET_ROLE_PREFIX = "datia_ds_"

    @classmethod
    def ensure_data_sources_dir(cls) -> str:
        d = settings.DATA_SOURCES_DIR
        os.makedirs(d, exist_ok=True)
        return d

    @classmethod
    def _activate_exclusively(cls, db: Session, conn_id: int) -> None:
        """Deja `conn_id` como la UNICA conexion activa.

        `is_active` es un booleano que el producto lee como un singleton: nueve
        lugares hacen `WHERE is_active = true` y tres de ellos `ORDER BY id DESC
        LIMIT 1`, o sea que "cual es la conexion activa" se responde por id, no
        por decision. Pero NINGUN sitio de escritura mantenia la exclusion:
        `upload_database_file` clavaba `is_active=True` sin tocar las demas.

        El efecto era acumulativo y por eso rompia para todos los roles a la vez.
        En la instalacion real quedaron dos conectores activos: la demo de
        plataforma (292, con las 74 filas de la matriz RBAC sembradas) y el
        dataset subido (2509, con cero permisos porque default-deny es lo
        correcto). El motor elegia 2509 por id, que es justo el que no tiene
        permisos: subir un dataset dejaba a todos los no-admin sin datos en todo
        el producto, y el sintoma se leia como "la IA no funciona con esta base".

        Por eso la exclusion vive ACA y no repetida en cada writer: todo lo que
        deje una conexion activa pasa por este metodo.

        No toca la matriz de permisos: activar un conector no concede acceso a
        nadie (default-deny, ver `upload_database_file`). Solo decide cual es la
        fuente de la consulta.
        """
        db.query(CorporateConnection).filter(
            CorporateConnection.is_active == True,
            CorporateConnection.id != conn_id,
        ).update({CorporateConnection.is_active: False}, synchronize_session=False)

    @classmethod
    def connector_to_out(cls, db: Session, conn: CorporateConnection) -> CorporateConnectionOut:
        """Arma la respuesta de un conector, incluidos los campos que NO son columnas.

        `detected_tables` (persistido como JSON) y `requires_permission_review`
        (True mientras la conexion no tenga ninguna fila de permiso) vivian solo
        en la respuesta del upload: al recargar, GET /connectors los perdia y la
        matriz de permisos del admin quedaba vacia.
        """
        tables = None
        if conn.detected_tables:
            try:
                parsed = json.loads(conn.detected_tables)
                tables = parsed if isinstance(parsed, list) else None
            except (TypeError, ValueError):
                tables = None
        has_permissions = db.query(RoleTablePermission).filter(
            RoleTablePermission.connection_id == conn.id
        ).count() > 0
        return CorporateConnectionOut(
            id=conn.id,
            name=conn.name,
            db_type=conn.db_type,
            host=conn.host,
            port=conn.port,
            database_name=conn.database_name,
            username=conn.username,
            is_active=conn.is_active,
            is_uploaded=conn.is_uploaded,
            requires_permission_review=bool(tables) and not has_permissions,
            detected_tables=tables,
            null_policy=conn.null_policy,
            created_at=conn.created_at,
        )

    @classmethod
    def list_connectors(cls, db: Session) -> List[CorporateConnectionOut]:
        conns = db.query(CorporateConnection).order_by(CorporateConnection.created_at.desc()).all()
        return [cls.connector_to_out(db, c) for c in conns]

    @classmethod
    def create_connector(cls, db: Session, conn_in: CorporateConnectionCreate) -> CorporateConnectionOut:
        existing = db.query(CorporateConnection).filter(CorporateConnection.name == conn_in.name).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ya existe una conexión registrada con ese nombre."
            )

        encrypted_pwd = encrypt_credential(conn_in.password) if conn_in.password else ""

        # Ensure dedicated individual PostgreSQL database exists on host if local server
        if (conn_in.db_type == DatabaseType.POSTGRESQL or str(conn_in.db_type).lower() == "postgresql") and conn_in.host in ("localhost", "127.0.0.1", settings.POSTGRES_SERVER):
            from sqlalchemy import create_engine, text
            clean_db = re.sub(r'[^a-zA-Z0-9_]', '_', conn_in.database_name.lower()).strip('_')
            if clean_db:
                maint_url = f"postgresql+psycopg://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_SERVER}:{settings.POSTGRES_PORT}/postgres"
                m_engine = create_engine(maint_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
                try:
                    with m_engine.connect() as m_conn:
                        exists = m_conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :dbname"), {"dbname": clean_db}).scalar()
                        if not exists:
                            m_conn.execute(text(f'CREATE DATABASE "{clean_db}" OWNER "{settings.POSTGRES_USER}"'))
                except Exception as exc:
                    # Antes era `except: pass`: faltaba privilegio CREATEDB o el
                    # nombre chocaba, el error se tragaba sin registrar nada, y
                    # se insertaba igual un CorporateConnection apuntando a una
                    # base inexistente con respuesta 201 Created. Ademas el
                    # dispose() se saltaba en el camino de error.
                    logger.error(
                        "No se pudo crear la base '%s' del conector '%s': %s",
                        clean_db, conn_in.name, exc,
                    )
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=(
                            f"No se pudo crear la base '{clean_db}' en el servidor PostgreSQL: {exc}. "
                            "Verifique que el usuario tenga privilegio CREATEDB y que el nombre no este en uso."
                        ),
                    ) from exc
                finally:
                    m_engine.dispose()

        new_conn = CorporateConnection(
            name=conn_in.name,
            db_type=conn_in.db_type,
            host=conn_in.host,
            port=conn_in.port,
            database_name=conn_in.database_name,
            username=conn_in.username or "admin",
            encrypted_password=encrypted_pwd,
            is_active=conn_in.is_active,
            is_uploaded=conn_in.is_uploaded
        )

        db.add(new_conn)
        db.commit()
        db.refresh(new_conn)

        if new_conn.is_active:
            cls._activate_exclusively(db, new_conn.id)
            db.commit()

        CatalogDomainService.seed_catalog_heuristics_for_connection(db, new_conn.id)

        return cls.connector_to_out(db, new_conn)

    @classmethod
    def _admin_engine(cls, db_name: str):
        """Engine administrativo a `db_name` (usuario de provision, AUTOCOMMIT).

        Se usa para crear/borrar bases y roles, y para los GRANT sobre objetos
        nuevos. La conexion de NEGOCIO del dataset nunca usa este usuario.
        """
        from sqlalchemy import create_engine

        url = (
            f"postgresql+psycopg://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
            f"@{settings.POSTGRES_SERVER}:{settings.POSTGRES_PORT}/{db_name}"
        )
        return create_engine(url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)

    @classmethod
    def _provision_dataset_role(cls, db_name: str, role_name: str, role_password: str) -> None:
        """Crea el rol de solo lectura del dataset y le concede SELECT.

        El usuario de provision (superusuario en la imagen oficial) solo se usa
        para crear e importar; la conexion guardada en `CorporateConnection` es
        este rol, sin NINGUN privilegio de escritura ni acceso a funciones de
        archivo (`pg_read_file` sigue siendo exclusivo de superusuario, no se
        toca). El `REVOKE ... FROM PUBLIC` es lo que impide que PUBLIC herede
        CONNECT o CREATE aunque el rol dedicado se equivoque.

        Todo se ejecuta con engines AUTOCOMMIT y desechables: si algo falla a
        mitad, el llamador limpia base y rol.
        """
        from sqlalchemy import text

        ident = quote_sql_identifier
        # El password no puede ir como bind parameter en un CREATE ROLE (utility
        # statement), asi que se cita como literal. `token_urlsafe` no produce
        # comillas, pero se escapa igual: el punto de entrada no confia en el.
        pwd_literal = "'" + role_password.replace("'", "''") + "'"

        m_engine = cls._admin_engine("postgres")
        try:
            with m_engine.connect() as m_conn:
                m_conn.execute(text(
                    f"CREATE ROLE {ident(role_name)} LOGIN NOSUPERUSER NOCREATEDB "
                    f"NOCREATEROLE NOINHERIT PASSWORD {pwd_literal}"
                ))
                m_conn.execute(text(f"REVOKE ALL ON DATABASE {ident(db_name)} FROM PUBLIC"))
                m_conn.execute(text(
                    f"GRANT CONNECT ON DATABASE {ident(db_name)} TO {ident(role_name)}"
                ))
        finally:
            m_engine.dispose()

        db_engine = cls._admin_engine(db_name)
        try:
            with db_engine.connect() as conn:
                conn.execute(text("REVOKE ALL ON SCHEMA public FROM PUBLIC"))
                conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {ident(role_name)}"))
                # Solo tablas: ni secuencias ni funciones. El camino de lectura no
                # necesita `nextval`, y no se concede EXECUTE a proposito.
                conn.execute(text(
                    f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {ident(role_name)}"
                ))
                # Tablas que se importen despues (mismo owner de provision) tambien
                # quedan legibles sin repetir el GRANT a mano.
                conn.execute(text(
                    f"ALTER DEFAULT PRIVILEGES FOR ROLE {ident(settings.POSTGRES_USER)} "
                    f"IN SCHEMA public GRANT SELECT ON TABLES TO {ident(role_name)}"
                ))
        finally:
            db_engine.dispose()

    @classmethod
    def _drop_dataset_role(cls, role_name: Optional[str]) -> None:
        """`DROP ROLE IF EXISTS` best-effort del rol dedicado (no del de provision)."""
        from sqlalchemy import text

        if not role_name or not role_name.startswith(cls.DATASET_ROLE_PREFIX):
            return
        m_engine = cls._admin_engine("postgres")
        try:
            with m_engine.connect() as m_conn:
                m_conn.execute(text(
                    f"DROP ROLE IF EXISTS {quote_sql_identifier(role_name)}"
                ))
        finally:
            m_engine.dispose()

    @classmethod
    async def upload_database_file(
        cls,
        db: Session,
        file: UploadFile,
        name: Optional[str] = None
    ) -> CorporateConnectionOut:
        ds_dir = cls.ensure_data_sources_dir()
        original_filename = file.filename or "uploaded_database.sqlite"
        clean_filename = "".join(c for c in original_filename if c.isalnum() or c in (".", "_", "-"))
        ext = os.path.splitext(clean_filename)[1].lower()

        # `.sql` NO se acepta. Ejecutar un script arbitrario contra la base daba
        # DDL/DML sin pasar por ASTValidator, con el usuario del conector (que en la
        # imagen oficial es superusuario), y los errores se tragaban reportando 201.
        # Conectar a Oracle/MySQL/MSSQL es otra funcionalidad (connectors) y no
        # depende de esto.
        allowed_exts = [".sqlite", ".db", ".sqlite3", ".csv", ".xlsx", ".xls", ".tsv", ".txt"]
        if ext not in allowed_exts:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Formato no soportado. Debe ser un archivo SQLite, Excel o CSV."
            )

        # NO se le mete un uuid acá a propósito: el importador toma el nombre de la tabla
        # del nombre de este archivo temporal (`os.path.splitext(basename)[0]` en
        # postgres_importer), así que un sufijo aleatorio acá renombra las tablas
        # del dataset. La colisión de este path es latente y se reporta aparte.
        unique_raw_name = f"raw_{int(time.time())}_{clean_filename}"
        raw_target_path = os.path.join(ds_dir, unique_raw_name)

        try:
            with open(raw_target_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error al guardar el archivo en el servidor: {str(e)}"
            )

        base_display_name = name.strip() if (name and name.strip()) else os.path.splitext(original_filename)[0]
        final_name = base_display_name
        counter = 1
        while db.query(CorporateConnection).filter(CorporateConnection.name == final_name).first():
            final_name = f"{base_display_name}_{counter}"
            counter += 1

        detected_tables = []
        pg_target_db_name = None

        from sqlalchemy import create_engine, text

        # Sanitize database name for PostgreSQL (lowercase alphanumeric and underscores)
        clean_db_name = re.sub(r'[^a-zA-Z0-9_]', '_', final_name.lower()).strip('_')
        if not clean_db_name or clean_db_name[0].isdigit():
            clean_db_name = f"db_{clean_db_name}"
        # El dedup de arriba (líneas 183-185) es sobre el NOMBRE del conector en
        # la metadata, y no dice nada de lo que hay en el SERVIDOR. Aquí se
        # reutilizaba la base si ya existía, y una base que quedó viva (un
        # conector borrado, una corrida interrumpida) arrastra sus tablas: la
        # importación de un archivo con el mismo nombre las encontraba ya
        # existía y el nombre de tabla salía deduplicado (`sensores_iot_1`).
        # El sufijo uuid hace el nombre único de verdad: único "en el servidor",
        # no "entre conectores que aún existen".
        clean_db_name = f"{int(time.time())}_{uuid.uuid4().hex[:8]}_{clean_db_name}"
        clean_db_name = clean_db_name[:50]
        pg_target_db_name = clean_db_name

        # Create dedicated PostgreSQL database. Con el nombre ya unico, la
        # reutilizacion de una base preexistente no puede ocurrir; si aparece,
        # es una colision de uuid y conviene que se vea en vez de importarse
        # encima de los datos de otro.
        maint_url = f"postgresql+psycopg://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_SERVER}:{settings.POSTGRES_PORT}/postgres"
        m_engine = create_engine(maint_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
        try:
            with m_engine.connect() as m_conn:
                exists = m_conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :dbname"), {"dbname": pg_target_db_name}).scalar()
                if exists:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"La base '{pg_target_db_name}' ya existe en el servidor. No se importa encima."
                    )
                m_conn.execute(text(f'CREATE DATABASE "{pg_target_db_name}" OWNER "{settings.POSTGRES_USER}"'))
        finally:
            m_engine.dispose()

        business_url = f"postgresql+psycopg://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_SERVER}:{settings.POSTGRES_PORT}/{pg_target_db_name}"
        pg_engine = create_engine(business_url, pool_pre_ping=True)
        try:
            detected_tables = convert_uploaded_file_to_postgres(
                source_path=raw_target_path,
                ext=ext,
                target_engine=pg_engine
            )
        except Exception as err:
            if os.path.exists(raw_target_path):
                try: os.remove(raw_target_path)
                except Exception: pass
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Error al procesar e importar archivo en PostgreSQL '{original_filename}': {str(err)}"
            )
        finally:
            pg_engine.dispose()
            if os.path.exists(raw_target_path):
                try: os.remove(raw_target_path)
                except Exception: pass

        # El dataset deja de consultarse con el usuario de provision (superusuario
        # en la imagen oficial de PostgreSQL): se crea un rol dedicado, aleatorio
        # y de solo lectura, y la conexion guardada apunta a el. Si la provision
        # falla se tira la base recien creada y se devuelve error: guardar una
        # conexion con el superusuario es justo lo que este camino evita.
        role_name = f"{cls.DATASET_ROLE_PREFIX}{uuid.uuid4().hex[:12]}"
        role_password = secrets.token_urlsafe(32)
        try:
            cls._provision_dataset_role(pg_target_db_name, role_name, role_password)
        except Exception as exc:
            logger.error(
                "No se pudo crear el rol de solo lectura del dataset '%s' (%s): %s",
                final_name, pg_target_db_name, exc,
            )
            try:
                cleanup_engine = cls._admin_engine("postgres")
                try:
                    with cleanup_engine.connect() as cleanup_conn:
                        cleanup_conn.execute(text(
                            f"DROP DATABASE IF EXISTS {quote_sql_identifier(pg_target_db_name)} "
                            "WITH (FORCE)"
                        ))
                finally:
                    cleanup_engine.dispose()
                cls._drop_dataset_role(role_name)
            except Exception as cleanup_exc:
                logger.error(
                    "Limpieza incompleta tras fallar el rol del dataset %s: %s",
                    pg_target_db_name, cleanup_exc,
                )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "No se pudo crear el usuario de solo lectura del dataset. "
                    "Verifique que el usuario de provision tenga privilegios CREATEROLE "
                    "y que la base se haya podido limpiar."
                ),
            ) from exc

        new_conn = CorporateConnection(
            name=final_name,
            db_type=DatabaseType.POSTGRESQL,
            host=settings.POSTGRES_SERVER,
            port=settings.POSTGRES_PORT,
            database_name=pg_target_db_name,
            username=role_name,
            encrypted_password=encrypt_credential(role_password),
            is_active=True,
            is_uploaded=True,
            detected_tables=json.dumps(detected_tables)
        )
        db.add(new_conn)
        db.commit()
        db.refresh(new_conn)

        # El dataset nuevo pasa a ser la unica fuente de la consulta: antes se
        # dejaba la anterior en `is_active=True` y el motor, que elige por
        # `ORDER BY id DESC`, se quedaba con este por defecto sin que nadie lo
        # hubiera pedido. Ver `_activate_exclusively`.
        cls._activate_exclusively(db, new_conn.id)
        db.commit()

        # Default-deny: subir un dataset NO concede acceso a nadie.
        #
        # Antes este bloque creaba un RoleTablePermission(is_allowed=True) por cada
        # (rol, tabla) y solo se saltaban unas cuantas tablas de la demo SAP,
        # detectadas por substring sobre el NOMBRE del rol. En cualquier dataset que
        # no fuera esa demo la lista de exclusion no excluye nada, asi que casi
        # todos los roles quedaban con las todas las tablas: la mascara por columna
        # de governance_guard seguia aplicando, pero el permiso de tabla que la
        # precede ya habia concedido el acceso. Default-allow por ausencia de
        # evidencia.
        #
        # Ahora no hay acceso hasta que un admin lo conceda con
        # PUT /api/v1/permissions (ConnectorDomainService.set_role_table_permissions).
        # `requires_permission_review=True` y `detected_tables` le dicen al admin
        # que hay tabla nueva sin nadie autorizado todavia.
        db.commit()
        db.refresh(new_conn)

        # Automatically seed heuristic semantic descriptions & data dictionary definitions
        CatalogDomainService.seed_catalog_heuristics_for_connection(
            db, new_conn.id, only_tables=detected_tables
        )

        return cls.connector_to_out(db, new_conn)

    @classmethod
    def update_connector(cls, db: Session, conn_id: int, conn_in: CorporateConnectionUpdate) -> CorporateConnectionOut:
        conn = db.query(CorporateConnection).filter(CorporateConnection.id == conn_id).first()
        if not conn:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conexión de base de datos no encontrada."
            )

        if conn_in.name and conn_in.name != conn.name:
            existing = db.query(CorporateConnection).filter(CorporateConnection.name == conn_in.name).first()
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Ya existe otra conexión registrada con ese nombre."
                )
            conn.name = conn_in.name

        if conn_in.db_type is not None:
            conn.db_type = conn_in.db_type
        if conn_in.host is not None:
            conn.host = conn_in.host
        if conn_in.port is not None:
            conn.port = conn_in.port
        if conn_in.database_name is not None:
            conn.database_name = conn_in.database_name
        if conn_in.username is not None:
            conn.username = conn_in.username
        if conn_in.password:
            conn.encrypted_password = encrypt_credential(conn_in.password)
        if conn_in.is_active is not None:
            conn.is_active = conn_in.is_active

        db.commit()
        db.refresh(conn)
        if conn.is_active:
            cls._activate_exclusively(db, conn.id)
            db.commit()
            db.refresh(conn)
        return cls.connector_to_out(db, conn)

    @classmethod
    def toggle_connector_active(cls, db: Session, conn_id: int) -> CorporateConnectionOut:
        conn = db.query(CorporateConnection).filter(CorporateConnection.id == conn_id).first()
        if not conn:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conexión de base de datos no encontrada."
            )
        conn.is_active = not conn.is_active
        db.commit()
        db.refresh(conn)
        if conn.is_active:
            # Encender una conexion apaga las demas: el toggle de la UI es la via
            # normal para cambiar de fuente, y si no las apagara el estado
            # "hay varias activas" volveria a ser alcanzable desde el producto.
            cls._activate_exclusively(db, conn.id)
            db.commit()
            db.refresh(conn)
        return cls.connector_to_out(db, conn)

    @classmethod
    def delete_connector(cls, db: Session, conn_id: int) -> Dict[str, Any]:
        conn = db.query(CorporateConnection).filter(CorporateConnection.id == conn_id).first()
        if not conn:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conexión de base de datos no encontrada."
            )

        # Si era un dataset subido a PostgreSQL, se limpia lo que la app creo para el.
        #
        # Antes la condicion era una lista NEGRA: si el nombre NO estaba en
        # ["democratizacion_empresa", "democratizacion_metadatos", "postgres"] hacia
        # DROP DATABASE; y si ESTABA, caia en la rama else que hacia DROP TABLE sobre
        # esa misma base compartida, con nombres de tabla sacados de RoleTablePermission.
        # O sea la proteccion estaba invertida: nombrar el conector
        # "democratizacion_metadatos" no salvaba la base de la plataforma, la llevaba
        # a la rama que le borra tablas.
        #
        # Ahora es explicito: una base dedicada que creo la app se puede tirar; una
        # base de plataforma no se toca de ninguna manera.
        platform_dbs = {
            settings.POSTGRES_DB,
            # El nombre de la base de metadata es fijo, no sale de la config.
            # Derivarlo de una ruta de archivo metia "" o None en el set cuando
            # esa ruta venia vacia, y con un POSTGRES_DB distinto la base de la
            # plataforma dejaba de protegerse. Fijo, siempre protegida.
            "democratizacion_metadatos",
            "democratizacion_empresa",
            "postgres",
            "template0",
            "template1",
        }

        if conn.is_uploaded and (conn.db_type == DatabaseType.POSTGRESQL or str(conn.db_type).lower() == "postgresql"):
            is_platform_db = conn.database_name in platform_dbs
            if conn.database_name and not is_platform_db:
                try:
                    from sqlalchemy import create_engine, text
                    maint_url = f"postgresql+psycopg://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_SERVER}:{settings.POSTGRES_PORT}/postgres"
                    m_engine = create_engine(maint_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
                    try:
                        with m_engine.connect() as m_conn:
                            m_conn.execute(text(
                                f"DROP DATABASE IF EXISTS {quote_sql_identifier(conn.database_name)} "
                                "WITH (FORCE)"
                            ))
                    finally:
                        m_engine.dispose()
                except Exception as exc:
                    # Antes era `except: pass` y la respuesta decia "eliminada
                    # correctamente" igual. Borrar la fila del conector sin haber
                    # liberado la base deja una base huerfana sin forma de recuperarla.
                    logger.error(
                        "No se pudo eliminar la base %s del conector %s: %s",
                        conn.database_name, conn_id, exc,
                    )
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=f"No se pudo eliminar la base '{conn.database_name}'. La conexión no se eliminó para no dejar datos huérfanos.",
                    ) from exc
            # Si es una base de plataforma se dejan las tablas: borrarlas seria
            # destructivo y el conector es solo una referencia administrative.
            #
            # El rol dedicado del dataset se tira DESPUES de la base: mientras la
            # base exista el rol tiene privilegios sobre sus objetos y PostgreSQL
            # rechaza el DROP ROLE. Solo se toca el prefijo que crea este servicio:
            # un conector subido antes de este cambio guarda `username` = usuario
            # de provision y borrarlo seria borrar el superusuario.
            try:
                cls._drop_dataset_role(conn.username)
            except Exception as exc:
                # La base ya no existe; dejar la fila del conector apuntando a una
                # base borrada seria peor que dejar un rol sin privilegios. Se
                # registra y se sigue.
                logger.warning(
                    "No se pudo eliminar el rol %s del dataset %s: %s",
                    conn.username, conn.database_name, exc,
                )

        # Todo lo que cuelga de la conexion se borra con ella: permisos huerfanos
        # con el mismo `connection_id` no deben sobrevivir a la conexion.
        db.query(SemanticCatalog).filter(SemanticCatalog.connection_id == conn_id).delete()
        db.query(RoleTablePermission).filter(RoleTablePermission.connection_id == conn_id).delete()
        db.query(RoleColumnPermission).filter(RoleColumnPermission.connection_id == conn_id).delete()

        db.delete(conn)
        db.commit()
        return {"message": f"Conexión '{conn.name}' eliminada correctamente.", "id": conn_id}

    @classmethod
    def set_role_table_permissions(
        cls,
        db: Session,
        connection_id: int,
        role_id: int,
        table_names: List[str],
        is_allowed: bool = True,
    ) -> List[RoleTablePermission]:
        """
        Concede o revoca acceso a tablas para un rol en una conexion. Es la unica
        via por la que un dataset obtiene acceso: el upload deja el dataset en
        default-deny y el admin decide aca, tabla por tabla.
        """
        conn = db.query(CorporateConnection).filter(CorporateConnection.id == connection_id).first()
        if not conn:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conexión no encontrada.")

        role = db.query(Role).filter(Role.id == role_id).first()
        if not role:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rol no encontrado.")

        schema_name = "public"

        touched = []
        for raw_name in table_names:
            tbl = (raw_name or "").strip()
            if not tbl:
                continue
            perm = db.query(RoleTablePermission).filter(
                RoleTablePermission.role_id == role_id,
                RoleTablePermission.connection_id == connection_id,
                RoleTablePermission.table_name == tbl,
            ).first()
            if perm:
                perm.is_allowed = is_allowed
                # Toda fila que pasa por aqui es decision de un admin, no el
                # auto-grant: es lo que la migracion de default-deny distingue.
                perm.granted_by_admin = True
                perm.schema_name = perm.schema_name or schema_name
            else:
                perm = RoleTablePermission(
                    role_id=role_id,
                    connection_id=connection_id,
                    schema_name=schema_name,
                    table_name=tbl,
                    is_allowed=is_allowed,
                    granted_by_admin=True,
                )
                db.add(perm)
            touched.append(perm)

        db.commit()

        # El schema cacheado del DynamicSchemaPruningService dura 10 minutos: sin
        # esto el admin concede el permiso y el usuario sigue sin ver la tabla
        # hasta que expire la cache.
        try:
            from app.modules.chat_engine.dynamic_schema import DynamicSchemaPruningService
            DynamicSchemaPruningService.invalidate_schema_cache(connection_id)
        except Exception as exc:
            logger.warning("No se pudo invalidar el cache de esquema tras cambiar permisos: %s", exc)

        for perm in touched:
            db.refresh(perm)
        return touched

    @classmethod
    def list_role_table_permissions(cls, db: Session, connection_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Matriz de permisos de tabla, para que el admin vea que concedio y que no."""
        q = db.query(RoleTablePermission)
        if connection_id is not None:
            q = q.filter(RoleTablePermission.connection_id == connection_id)
        rows = q.order_by(
            RoleTablePermission.connection_id,
            RoleTablePermission.role_id,
            RoleTablePermission.table_name,
        ).all()
        out = []
        for r in rows:
            role = db.query(Role).filter(Role.id == r.role_id).first()
            out.append({
                "id": r.id,
                "connection_id": r.connection_id,
                "role_id": r.role_id,
                "role_name": role.name if role else None,
                "schema_name": r.schema_name,
                "table_name": r.table_name,
                "is_allowed": bool(r.is_allowed),
                "granted_by_admin": bool(getattr(r, "granted_by_admin", False)),
            })
        return out

    @classmethod
    def list_role_column_permissions(
        cls,
        db: Session,
        connection_id: Optional[int] = None,
        role_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Politicas de columna (ALLOWED/MASKED/BLOCKED) por rol y conexion.

        Es el espejo de `list_role_table_permissions` para CLS: una fila dice que
        una columna concreta se permite, se enmascara o no se devuelve. Sin fila
        la columna queda legible, salvo que otra tabla de la MISMA conexion
        proteja ese nombre (ver el docstring de `set_role_column_permissions`).
        """
        q = db.query(RoleColumnPermission)
        if connection_id is not None:
            q = q.filter(RoleColumnPermission.connection_id == connection_id)
        if role_id is not None:
            q = q.filter(RoleColumnPermission.role_id == role_id)
        rows = q.order_by(
            RoleColumnPermission.connection_id,
            RoleColumnPermission.role_id,
            RoleColumnPermission.table_name,
            RoleColumnPermission.column_name,
        ).all()
        role_names = dict(db.query(Role.id, Role.name).all())
        return [
            {
                "id": r.id,
                "connection_id": r.connection_id,
                "role_id": r.role_id,
                "role_name": role_names.get(r.role_id),
                "schema_name": r.schema_name or "public",
                "table_name": r.table_name,
                "column_name": r.column_name,
                "permission_type": r.permission_type.value,
            }
            for r in rows
        ]

    @staticmethod
    def _validate_column_policy_scope(
        db: Session, connection_id: int, role_id: int
    ) -> None:
        """Valida el alcance de una politica de columna.

        Compartido por el PUT y el DELETE: 404 si la conexion o el rol no
        existen, 400 si es el rol admin (exento de CLS por diseño). Un mismo
        criterio en ambos caminos evita que el reset acepte un alcance que el
        alta rechaza.
        """
        conn = db.query(CorporateConnection).filter(
            CorporateConnection.id == connection_id
        ).first()
        if not conn:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conexión no encontrada.")

        role = db.query(Role).filter(Role.id == role_id).first()
        if not role:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rol no encontrado.")
        if role.name in ADMIN_ROLES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "El rol de administrador está exento de políticas de columna por diseño: "
                    "una regla suya no la aplicaría ningún camino del producto."
                ),
            )

    @classmethod
    def set_role_column_permissions(
        cls,
        db: Session,
        connection_id: int,
        role_id: int,
        columns: List[Dict[str, str]],
        permission_type: ColumnPermissionType,
    ) -> List[RoleColumnPermission]:
        """Crea o actualiza politicas de columna para un rol en una conexion.

        Upsert por (rol, conexion, tabla, columna): repetir el PUT actualiza la
        fila existente en vez de acumular duplicados que el guard resolveria con
        el ultimo valor leido.

        NO valida que la columna exista en la base fisica: una politica sobre una
        columna inexistente es inerte, y comprobarlo exigiria una introspeccion en
        cada PUT (que puede fallar por red cuando la base del cliente esta caida).
        La UI ofrece solo columnas del diccionario real. El rol admin se rechaza
        porque el guard lo exime de CLS: sus filas serian mentira visible en la UI.

        Alcance real y deliberado: el guard resuelve BLOCKED/MASKED por NOMBRE de
        columna dentro de la conexion (`dynamic_schema` y `mask_rows`), asi que si
        dos tablas comparten nombre gana la politica mas restrictiva. Sobre-bloquear
        es la postura fail-closed del proyecto; lo contrario filtraria datos.
        """
        cls._validate_column_policy_scope(db, connection_id, role_id)

        touched: List[RoleColumnPermission] = []
        seen: set = set()
        for entry in columns:
            table = (entry.get("table_name") or "").strip()
            column = (entry.get("column_name") or "").strip()
            if not table or not column:
                continue
            key = (table.lower(), column.lower())
            if key in seen:
                continue
            seen.add(key)

            perm = db.query(RoleColumnPermission).filter(
                RoleColumnPermission.role_id == role_id,
                RoleColumnPermission.connection_id == connection_id,
                func.lower(RoleColumnPermission.table_name) == table.lower(),
                func.lower(RoleColumnPermission.column_name) == column.lower(),
            ).first()
            if perm:
                perm.permission_type = permission_type
                perm.schema_name = perm.schema_name or "public"
            else:
                perm = RoleColumnPermission(
                    role_id=role_id,
                    connection_id=connection_id,
                    schema_name="public",
                    table_name=table,
                    column_name=column,
                    permission_type=permission_type,
                )
                db.add(perm)
            touched.append(perm)

        if not touched:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No se recibió ninguna columna válida para aplicar la política.",
            )

        db.commit()

        # Mismo motivo que en `set_role_table_permissions`: el prompt cacheado
        # seguiria mostrando la columna (o su muestra) hasta 10 minutos despues de
        # que el admin la bloqueo.
        try:
            from app.modules.chat_engine.dynamic_schema import DynamicSchemaPruningService
            DynamicSchemaPruningService.invalidate_schema_cache(connection_id)
        except Exception as exc:
            logger.warning("No se pudo invalidar el cache de esquema tras cambiar políticas de columna: %s", exc)

        for perm in touched:
            db.refresh(perm)
        return touched

    @classmethod
    def delete_role_column_permission(
        cls,
        db: Session,
        connection_id: int,
        role_id: int,
        table_name: str,
        column_name: str,
    ) -> bool:
        """Borra la fila CLS y devuelve la columna a "sin politica".

        Es el unico camino para deshacer una excepcion `ALLOWED`: el seed solo
        inserta cuando no existe fila, asi que borrar deja la columna en el
        default que corresponda al proximo arranque. Idempotente: si no habia
        fila devuelve False en vez de 404 (la UI puede pedir el reset dos veces).
        """
        cls._validate_column_policy_scope(db, connection_id, role_id)

        table = (table_name or "").strip()
        column = (column_name or "").strip()
        if not table or not column:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tabla y columna son obligatorias para borrar una política.",
            )

        row = db.query(RoleColumnPermission).filter(
            RoleColumnPermission.connection_id == connection_id,
            RoleColumnPermission.role_id == role_id,
            func.lower(RoleColumnPermission.table_name) == table.lower(),
            func.lower(RoleColumnPermission.column_name) == column.lower(),
        ).first()
        if not row:
            return False

        db.delete(row)
        db.commit()

        # Mismo motivo que en el PUT: el prompt cacheado seguiria enmascarando
        # (o bloqueando) la columna hasta 10 minutos despues del reset.
        try:
            from app.modules.chat_engine.dynamic_schema import DynamicSchemaPruningService
            DynamicSchemaPruningService.invalidate_schema_cache(connection_id)
        except Exception as exc:
            logger.warning(
                "No se pudo invalidar el cache de esquema tras borrar la política de columna: %s",
                exc,
            )

        return True

    @classmethod
    def test_connection_connectivity(cls, test_in: ConnectionTestRequest) -> ConnectionTestResult:
        result = HealthService.check_db_connectivity(
            host=test_in.host,
            port=test_in.port,
            db_type=test_in.db_type.value.upper(),
            database_name=test_in.database_name
        )
        return ConnectionTestResult(
            success=result["success"],
            message=result["message"],
            latency_ms=result["latency_ms"]
        )

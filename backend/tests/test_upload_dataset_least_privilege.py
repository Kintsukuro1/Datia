"""El dataset subido se consulta con un rol dedicado de SOLO LECTURA.

Antes, `upload_database_file` guardaba en `CorporateConnection` el usuario de
provision (`settings.POSTGRES_USER`), que en la imagen oficial de PostgreSQL es
superusuario. El motor de negocio abria la conexion del dataset con el y el chat
solo evitaba escrituras por el ASTValidator: cualquier camino que no pasara por
ahi (o un bug del validador) corria con privilegios de superusuario, incluido
`pg_read_file`.

Ahora cada subida crea `datia_ds_<uuid>` con LOGIN NOSUPERUSER NOCREATEDB
NOCREATEROLE NOINHERIT, CONNECT en su base y SELECT en las tablas. Estos tests
lo verifican contra el PostgreSQL real: no con mocks, porque el bug vivia en los
privilegios reales del motor.
"""
import io
import unittest
import uuid
from urllib.parse import quote_plus

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from conftest import make_auth_headers, pg_source_engine

from main import app
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import decrypt_credential
from app.db.init_db import init_db
from app.modules.admin_catalog.models import CorporateConnection, DatabaseType
from app.modules.catalog.services.connector_service import ConnectorDomainService


class TestUploadedDatasetLeastPrivilege(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        db = SessionLocal()
        init_db(db)
        db.close()

    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()
        self.headers = make_auth_headers(self.db, "admin")
        # Nombre unico por test: una corrida interrumpida no debe chocar con la
        # siguiente por el UniqueConstraint de `corporate_connections.name`.
        self.name = f"probe-minpriv-{uuid.uuid4().hex[:8]}"
        self.role_engine = None

    def tearDown(self):
        try:
            if self.role_engine is not None:
                self.role_engine.dispose()

            conn = self.db.query(CorporateConnection).filter(
                CorporateConnection.name == self.name).first()
            if conn:
                try:
                    # Misma via que el producto: borra base, rol, permisos y fila.
                    ConnectorDomainService.delete_connector(self.db, conn.id)
                except Exception:
                    # Si el servicio fallo ya no se puede confiar en su limpieza:
                    # se tira todo a mano para no dejar base ni rol huerfanos.
                    self.db.rollback()
                    self._force_cleanup(conn.database_name, conn.username)
                    self.db.query(CorporateConnection).filter(
                        CorporateConnection.id == conn.id).delete()
                    self.db.commit()
        finally:
            self.db.close()

    def _force_cleanup(self, db_name, role_name):
        # AUTOCOMMIT: DROP DATABASE no puede correr dentro de una transaccion.
        url = (
            f"postgresql+psycopg://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
            f"@{settings.POSTGRES_SERVER}:{settings.POSTGRES_PORT}/postgres"
        )
        eng = create_engine(url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
        try:
            with eng.connect() as c:
                if db_name:
                    c.execute(text(
                        f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'
                    ))
                if role_name and role_name.startswith(
                        ConnectorDomainService.DATASET_ROLE_PREFIX):
                    c.execute(text(f'DROP ROLE IF EXISTS "{role_name}"'))
        finally:
            eng.dispose()

    def _upload_csv(self):
        csv = b"id,nombre\n1,Ana\n2,Beto\n"
        res = self.client.post(
            "/api/v1/connectors/upload",
            files={"file": ("probe_minpriv.csv", io.BytesIO(csv), "text/csv")},
            data={"name": self.name},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 201, res.text[:500])
        return res.json()

    def _role_engine_for(self, conn):
        password = decrypt_credential(conn.encrypted_password)
        url = (
            f"postgresql+psycopg://{quote_plus(conn.username)}:{quote_plus(password)}"
            f"@{conn.host}:{conn.port}/{conn.database_name}"
        )
        return create_engine(url, pool_pre_ping=True)

    @staticmethod
    def _sqlstate(exc):
        """SQLSTATE del error de psycopg envuelto por SQLAlchemy (42501 = permisos)."""
        return getattr(getattr(exc, "orig", None), "sqlstate", None)

    def test_uploaded_dataset_connects_with_a_read_only_role(self):
        data = self._upload_csv()
        conn = self.db.query(CorporateConnection).filter(
            CorporateConnection.id == data["id"]).one()

        self.assertNotEqual(
            conn.username, settings.POSTGRES_USER,
            "la conexion del dataset apunta al usuario de provision (superusuario)",
        )
        self.assertTrue(
            conn.username.startswith(ConnectorDomainService.DATASET_ROLE_PREFIX),
            f"rol inesperado: {conn.username}",
        )

        with pg_source_engine("postgres").connect() as c:
            attrs = c.execute(text(
                "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = :n"
            ), {"n": conn.username}).one()
        self.assertEqual(
            tuple(attrs), (False, False, False),
            f"el rol {conn.username} conserva privilegios administrativos: {attrs}",
        )

        table = data["detected_tables"][0]
        self.role_engine = self._role_engine_for(conn)

        # 1. SELECT funciona con las credenciales guardadas.
        with self.role_engine.connect() as c:
            rows = c.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar()
        self.assertEqual(rows, 2, "el rol de solo lectura no puede leer su dataset")

        # 2. Nada de escritura ni de lectura de archivos del servidor.
        denied = (
            f'CREATE TABLE probe_debe_fallar (x int)',
            f'DELETE FROM "{table}"',
            "SELECT pg_read_file('pg_hba.conf')",
        )
        for sql in denied:
            with self.subTest(sql=sql):
                with self.assertRaises(Exception) as ctx:
                    with self.role_engine.connect() as c:
                        c.execute(text(sql))
                self.assertEqual(
                    self._sqlstate(ctx.exception), "42501",
                    f"'{sql}' fallo por otra causa que permisos: {ctx.exception}",
                )

    def test_delete_connector_drops_the_dataset_role(self):
        data = self._upload_csv()
        conn = self.db.query(CorporateConnection).filter(
            CorporateConnection.id == data["id"]).one()
        role_name, db_name = conn.username, conn.database_name

        res = self.client.delete(
            f"/api/v1/connectors/{data['id']}", headers=self.headers)
        self.assertEqual(res.status_code, 200, res.text[:500])

        self.assertIsNone(
            self.db.query(CorporateConnection).filter(
                CorporateConnection.id == data["id"]).first(),
            "la fila del conector sobrevivio al delete",
        )

        eng = pg_source_engine("postgres")
        try:
            with eng.connect() as c:
                self.assertFalse(
                    c.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :n"),
                              {"n": role_name}).scalar(),
                    f"el rol {role_name} quedo huerfano tras borrar el conector",
                )
                self.assertFalse(
                    c.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"),
                              {"n": db_name}).scalar(),
                    f"la base {db_name} quedo huerfana tras borrar el conector",
                )
        finally:
            eng.dispose()

    def test_delete_connector_does_not_drop_roles_it_did_not_create(self):
        """El guard es el prefijo del rol, no `is_uploaded`.

        Un conector manual guarda el usuario que el admin escribio: si el delete
        lo borrara, una conexion manual con el usuario de provision apagaria la
        plataforma entera al eliminarse.
        """
        role_name = f"probe_manual_{uuid.uuid4().hex[:8]}"
        admin_eng = create_engine(
            f"postgresql+psycopg://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
            f"@{settings.POSTGRES_SERVER}:{settings.POSTGRES_PORT}/postgres",
            isolation_level="AUTOCOMMIT", pool_pre_ping=True,
        )
        with admin_eng.connect() as c:
            c.execute(text(f'CREATE ROLE "{role_name}"'))

        try:
            conn = CorporateConnection(
                name=self.name,
                db_type=DatabaseType.POSTGRESQL,
                host=settings.POSTGRES_SERVER,
                port=settings.POSTGRES_PORT,
                database_name="democratizacion_empresa",
                username=role_name,
                encrypted_password="",
                is_active=False,
                is_uploaded=False,
            )
            self.db.add(conn)
            self.db.commit()
            self.db.refresh(conn)

            res = self.client.delete(
                f"/api/v1/connectors/{conn.id}", headers=self.headers)
            self.assertEqual(res.status_code, 200, res.text[:300])
            with admin_eng.connect() as c:
                exists = c.execute(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :n"),
                    {"n": role_name}).scalar()
            self.assertTrue(
                exists,
                "delete_connector borro un rol que no fue creado por el upload",
            )
        finally:
            with admin_eng.connect() as c:
                c.execute(text(f'DROP ROLE IF EXISTS "{role_name}"'))
            admin_eng.dispose()


if __name__ == "__main__":
    unittest.main()

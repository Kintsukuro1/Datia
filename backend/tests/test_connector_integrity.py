"""
Bugs 7-8-10: CREATE DATABASE fallido se tragaba y el conector moría con 201,
colisión de ruta con dos uploads del mismo archivo en el mismo segundo, y
clasificación de rolesTI/Financieros por subcadena en vez de por token.
"""
import io
import re
import unittest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from sqlalchemy import text

from conftest import make_auth_headers, pg_source_engine

from main import app
from app.core.config import settings
from app.core.database import SessionLocal
from app.db.init_db import init_db
from app.modules.admin_catalog.models import CorporateConnection, DatabaseType
from app.modules.admin_catalog.schemas import CorporateConnectionCreate
from app.modules.catalog.services.connector_service import ConnectorDomainService
from app.modules.catalog.services.catalog_service import CatalogDomainService


def _role_matches_ti(role_name: str) -> bool:
    """Replica exacta de la regla del servicio para poder testearla aislada."""
    tokens = set(re.findall(r"[a-z0-9]+", role_name.lower()))
    return ("ti" in tokens) or ("infraestructura" in tokens)


def _role_matches_fin(role_name: str) -> bool:
    tokens = set(re.findall(r"[a-z0-9]+", role_name.lower()))
    return bool(tokens & {"economista", "financiero"})


class TestConnectorCreateDatabaseFailure(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        db = SessionLocal()
        init_db(db)
        db.close()

    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()
        # Admin con sesion viva: el preambulo comun vive en conftest.
        self.headers = make_auth_headers(self.db, "admin")

    def tearDown(self):
        for name in ("probe_createdb_falla",):
            self.db.query(CorporateConnection).filter(
                CorporateConnection.name == name).delete()
            self.db.commit()
        self.db.close()

    def test_failed_create_database_does_not_return_201_and_inserts_nothing(self):
        """BUG 7: el `except Exception: pass` se tragaba la falta de privilegio
        CREATEDB y el servicio insertaba igual un CorporateConnection apuntando a
        una base inexistente, respondiendo 201 Created."""
        conn_in = CorporateConnectionCreate(
            name="probe_createdb_falla",
            db_type=DatabaseType.POSTGRESQL,
            host=settings.POSTGRES_SERVER,
            port=settings.POSTGRES_PORT,
            database_name="probe_createdb_db",
            username="postgres",
            password="x",
        )

        boom = MagicMock()
        boom.execute.side_effect = RuntimeError("permission denied to create database")

        m_engine = MagicMock()
        m_conn_ctx = MagicMock()
        m_conn_ctx.__enter__.return_value = boom
        m_engine.connect.return_value = m_conn_ctx

        with patch("sqlalchemy.create_engine", return_value=m_engine):
            with self.assertRaises(Exception) as ctx:
                ConnectorDomainService.create_connector(self.db, conn_in)

        self.assertEqual(getattr(ctx.exception, "status_code", None), 502)
        self.assertIsNone(
            self.db.query(CorporateConnection).filter(
                CorporateConnection.name == "probe_createdb_falla").first(),
            "se insertó un conector apuntando a una base que nunca se creó",
        )

    def test_engine_is_disposed_even_on_failure(self):
        """El dispose() también se saltaba en el camino de error."""
        conn_in = CorporateConnectionCreate(
            name="probe_createdb_falla",
            db_type=DatabaseType.POSTGRESQL,
            host=settings.POSTGRES_SERVER,
            port=settings.POSTGRES_PORT,
            database_name="probe_createdb_db2",
            username="postgres",
            password="x",
        )
        boom = MagicMock()
        boom.execute.side_effect = RuntimeError("boom")
        m_engine = MagicMock()
        m_conn_ctx = MagicMock()
        m_conn_ctx.__enter__.return_value = boom
        m_engine.connect.return_value = m_conn_ctx

        with patch("sqlalchemy.create_engine", return_value=m_engine):
            with self.assertRaises(Exception):
                ConnectorDomainService.create_connector(self.db, conn_in)

        m_engine.dispose.assert_called_once()

    def test_creation_succeeds_when_database_exists(self):
        """El camino bueno no se rompió: si la base ya existe, se crea el conector."""
        conn_in = CorporateConnectionCreate(
            name="probe_createdb_falla",
            db_type=DatabaseType.POSTGRESQL,
            host=settings.POSTGRES_SERVER,
            port=settings.POSTGRES_PORT,
            database_name="probe_createdb_db3",
            username="postgres",
            password="x",
        )
        conn = MagicMock()
        conn.execute.return_value.scalar.return_value = 1  # ya existe
        m_engine = MagicMock()
        m_conn_ctx = MagicMock()
        m_conn_ctx.__enter__.return_value = conn
        m_engine.connect.return_value = m_conn_ctx

        with patch("sqlalchemy.create_engine", return_value=m_engine):
            with patch.object(
                CatalogDomainService, "seed_catalog_heuristics_for_connection"
            ):
                created = ConnectorDomainService.create_connector(self.db, conn_in)

        self.assertIsNotNone(created.id)
        # No se intentó el CREATE DATABASE porque ya existía
        statements = [str(c.args[0]) for c in conn.execute.call_args_list]
        self.assertFalse(any("CREATE DATABASE" in s for s in statements))


class TestUploadPathCollision(unittest.TestCase):
    """BUG 8: dos subidas del mismo archivo en el mismo segundo no pueden
    compartir base.

    Antes el nombre del archivo salia de `int(time.time())`: dos uploads de
    datos.csv en el mismo segundo producian dos conectores (datos y datos_1)
    apuntando al MISMO archivo, el segundo import pisaba al primero y borrar uno
    dejaba al otro apuntando a la nada. Hoy cada subida crea su propia base
    PostgreSQL con sufijo uuid, asi que el reloj no puede colisionarlas.
    """

    @classmethod
    def setUpClass(cls):
        db = SessionLocal()
        init_db(db)
        db.close()

    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()
        # Admin con sesion viva: el preambulo comun vive en conftest.
        self.headers = make_auth_headers(self.db, "admin")
        self.names = []

    def tearDown(self):
        # Se borra por la misma via que el producto: borrar la fila a mano
        # dejaria la base PostgreSQL subida viva y huerfana en el servidor.
        from app.modules.catalog.services.connector_service import ConnectorDomainService

        for name in self.names:
            conn = self.db.query(CorporateConnection).filter(
                CorporateConnection.name == name).first()
            if conn:
                ConnectorDomainService.delete_connector(self.db, conn.id)
        self.db.commit()
        self.db.close()

    def _upload_csv(self, filename, name):
        """Sube un CSV: el importador crea una base PostgreSQL dedicada."""
        csv = b"col_a,col_b\n1,2\n3,4\n"
        return self.client.post(
            "/api/v1/connectors/upload",
            files={"file": (filename, io.BytesIO(csv), "text/csv")},
            data={"name": name},
            headers=self.headers,
        )

    def _db_exists(self, db_name):
        eng = pg_source_engine("postgres")
        try:
            with eng.connect() as c:
                return bool(c.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": db_name}
                ).scalar())
        finally:
            eng.dispose()

    def _table_row_count(self, db_name):
        """Cuenta las filas de la unica tabla del dataset subido."""
        eng = pg_source_engine(db_name)
        try:
            with eng.connect() as c:
                tables = [r[0] for r in c.execute(text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                ))]
                self.assertTrue(tables, f"{db_name} no tiene ninguna tabla")
                return c.execute(text(f'SELECT COUNT(*) FROM "{tables[0]}"')).scalar()
        finally:
            eng.dispose()

    def test_two_uploads_of_same_file_in_same_second_get_distinct_databases(self):
        with patch("app.modules.catalog.services.connector_service.time.time",
                   return_value=1700000000.0):
            r1 = self._upload_csv("datos.csv", "probe-datos-1")
            r2 = self._upload_csv("datos.csv", "probe-datos-2")

        self.assertEqual(r1.status_code, 201, r1.text[:300])
        self.assertEqual(r2.status_code, 201, r2.text[:300])
        self.names = ["probe-datos-1", "probe-datos-2"]

        db1 = r1.json()["database_name"]
        db2 = r2.json()["database_name"]
        self.assertNotEqual(
            db1, db2,
            f"las dos subidas comparten base: {db1}",
        )

        # Y el segundo import NO pisa el contenido del primero: cada base tiene
        # su tabla con las 2 filas del CSV.
        self.assertEqual(self._table_row_count(db1), 2, f"contenido inesperado en {db1}")
        self.assertEqual(self._table_row_count(db2), 2, f"contenido inesperado en {db2}")

    def test_same_file_uploaded_twice_does_not_drop_the_other_database(self):
        """Borrar un conector no se lleva la base del otro."""
        with patch("app.modules.catalog.services.connector_service.time.time",
                   return_value=1700000100.0):
            r1 = self._upload_csv("alias.csv", "probe-alias-1")
            r2 = self._upload_csv("alias.csv", "probe-alias-2")
        self.assertEqual(r1.status_code, 201, r1.text[:300])
        self.assertEqual(r2.status_code, 201, r2.text[:300])
        self.names = ["probe-alias-1", "probe-alias-2"]

        db1, db2 = r1.json()["database_name"], r2.json()["database_name"]
        del_res = self.client.delete(
            f"/api/v1/connectors/{r1.json()['id']}", headers=self.headers)
        self.assertEqual(del_res.status_code, 200, del_res.text[:300])

        self.assertFalse(self._db_exists(db1), "la base del conector eliminado quedo viva")
        self.assertTrue(self._db_exists(db2), "borrar un conector se llevo la base del otro")


class TestRoleTokenMatching(unittest.TestCase):

    def test_ti_substring_false_positives_are_gone(self):
        """BUG 10: `"ti" in role.name.lower()` es busqueda de subcadena, asi que
        estos roles perdiaban tablas de negocio sin ser de TI."""
        for name in ("Analítica", "Autenticación", "Capital Intelectivo",
                     "Notificaciones", "Política", "Calidad", "Gestión"):
            with self.subTest(role=name):
                self.assertFalse(_role_matches_ti(name), f"'{name}' se clasificó como TI")

    def test_real_ti_roles_still_match(self):
        """Los roles TI de verdad siguen clasificándose como TI."""
        for name in ("TI", "Ingeniero de Infraestructura & TI", "TI Junior"):
            with self.subTest(role=name):
                self.assertTrue(_role_matches_ti(name))

    def test_financial_roles_match_by_token(self):
        self.assertTrue(_role_matches_fin("Economista"))
        self.assertTrue(_role_matches_fin("Analista Financiero & Comercial"))
        self.assertFalse(_role_matches_fin("Analítica"))
        self.assertFalse(_role_matches_fin("Director Ejecutivo (C-Level)"))

    def test_service_source_uses_token_match_not_substring(self):
        """Guardarra contra volver al `in` de subcadena."""
        import inspect
        src = inspect.getsource(ConnectorDomainService.upload_database_file)
        self.assertNotIn('"ti", "infraestructura"]', src)
        self.assertNotIn('k in role.name.lower()', src)
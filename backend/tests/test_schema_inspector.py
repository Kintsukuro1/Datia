"""SchemaInspector no puede reportar un fallo de conexion como "0 tablas".

Escenario real: password mal descifrada, Postgres caido, o un usuario sin
USAGE sobre el schema. `introspect_connection_metadata` se tragaba la excepcion
y devolvia `[]`, que es indistinguible de una base vacia. El diccionario
respondia HTTP 200 con `DataDictionaryResponse(total_tables=0)` y el frontend
le mostraba al usuario "esta base no tiene tablas".

El mismo archivo (y el mismo patron) tiene el bug del engine sin dispose():
`core/database.connector_engine` existe exactamente para eso y ya lo usan
sql_executor.py y dynamic_schema.py.
"""

import unittest
from unittest.mock import MagicMock, patch

from app.modules.admin_catalog.models import CorporateConnection, DatabaseType
from app.modules.catalog.services.schema_inspector import SchemaInspector, SchemaIntrospectionError


class TestConnectionFailureIsNotAnEmptyDatabase(unittest.TestCase):
    def test_postgres_connection_failure_raises_instead_of_returning_empty_list(self):
        conn = MagicMock()
        conn.db_type = DatabaseType.POSTGRESQL
        conn.name = "Produccion"
        conn.is_uploaded = False

        import app.core.database as core_db

        def boom(_conn):
            raise OperationalErrorShim("could not connect to server: connection refused")

        with patch.object(core_db, "connector_engine", boom):
            with self.assertRaises(SchemaIntrospectionError) as ctx:
                SchemaInspector.introspect_connection_metadata(conn)

        self.assertIn("Produccion", str(ctx.exception))

    def test_the_error_message_does_not_claim_the_database_is_empty(self):
        conn = MagicMock()
        conn.db_type = DatabaseType.POSTGRESQL
        conn.name = "Produccion"
        conn.is_uploaded = False

        import app.core.database as core_db

        def boom(_conn):
            raise OperationalErrorShim("password authentication failed")

        with patch.object(core_db, "connector_engine", boom):
            with self.assertRaises(SchemaIntrospectionError) as ctx:
                SchemaInspector.introspect_connection_metadata(conn)

        msg = str(ctx.exception).lower()
        self.assertNotIn("no tiene tablas", msg)
        self.assertNotIn("0 tablas", msg)

    def test_schema_introspection_error_is_a_distinct_exception_type(self):
        """El caller tiene que poder distinguirlo de un error de negocio."""
        self.assertTrue(issubclass(SchemaIntrospectionError, Exception))
        self.assertFalse(issubclass(SchemaIntrospectionError, AssertionError))


class OperationalErrorShim(Exception):
    pass


class TestNonPostgresConnectionIsNotAnEmptyDatabase(unittest.TestCase):
    """Una conexion que no se puede abrir devolvia `[]` (200 con
    total_tables=0), indistinguible de "esta base no tiene tablas". Ahora lanza
    nombrando la conexion."""

    def test_unsupported_engine_raises_naming_the_connection(self):
        conn = MagicMock()
        conn.db_type = "mysql"
        conn.name = "Clientes"
        conn.host = "db.interna"
        conn.port = 3306
        conn.database_name = "clientes"
        conn.username = "admin"
        conn.encrypted_password = None

        with self.assertRaises(SchemaIntrospectionError) as ctx:
            SchemaInspector.introspect_connection_metadata(conn)

        self.assertIn("Clientes", str(ctx.exception))


class TestResolveNeverFallsBackToTheDemoDatabase(unittest.TestCase):
    """Sin conexiones registradas no hay base que introspeccionar: antes se
    fabricaba una conexion SQLite falsa sobre la demo interna y el diccionario
    respondia las tablas de OTRA base como si fueran de la pedida."""

    @staticmethod
    def _db_returning(conn):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = conn
        db.query.return_value.order_by.return_value.first.return_value = conn
        return db

    def test_no_registered_connection_raises_instead_of_pointing_at_a_demo(self):
        db = MagicMock()
        # Las tres cadenas que usa `resolve_connection` sin connection_id:
        # filter().first(), filter().order_by().first() y order_by().first().
        db.query.return_value.filter.return_value.first.return_value = None
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        db.query.return_value.order_by.return_value.first.return_value = None

        with self.assertRaises(SchemaIntrospectionError) as ctx:
            SchemaInspector.resolve_connection(db, None)

        self.assertIn("conexión corporativa", str(ctx.exception).lower())

    def test_resolve_returns_the_connection_it_found(self):
        conn = CorporateConnection(
            id=43, name="Clientes", db_type=DatabaseType.POSTGRESQL,
            host="localhost", port=5432, database_name="clientes", username="admin",
            is_active=True,
        )

        resolved = SchemaInspector.resolve_connection(self._db_returning(conn), 43)

        self.assertIs(resolved, conn)


class TestPostgresEngineIsDisposed(unittest.TestCase):
    def test_connector_engine_is_the_shared_context_manager(self):
        """Verifica que el helper realmente dispone el engine al salir."""
        from contextlib import contextmanager
        disposed = []

        @contextmanager
        def fake(_conn):
            eng = MagicMock()
            try:
                yield eng
            finally:
                disposed.append(True)

        import app.core.database as core_db
        original = core_db.connector_engine
        core_db.connector_engine = fake
        try:
            conn = MagicMock()
            conn.db_type = DatabaseType.POSTGRESQL
            conn.name = "X"
            conn.is_uploaded = False
            try:
                SchemaInspector.introspect_connection_metadata(conn)
            except Exception:
                # Da igual si la introspeccion falla contra el MagicMock:
                # lo que se verifica es que el engine se libero igual.
                pass
        finally:
            core_db.connector_engine = original

        self.assertEqual(len(disposed), 1, "el engine no se libero al salir de la introspeccion")


if __name__ == "__main__":
    unittest.main()
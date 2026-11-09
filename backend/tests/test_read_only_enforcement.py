"""La garantia de solo lectura no puede degradarse en silencio.

`execute_raw_sql` abria la transaccion con `SET TRANSACTION READ ONLY` dentro de
un `try/except: pass`. Si el SET fallaba, la consulta seguia y el unico freno
era el AST. Estos tests fijan que el fallo de la segunda capa cancele la
consulta, y que el engine del conector nazca con `default_transaction_read_only`
y `statement_timeout`.
"""
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from app.core.config import settings
from app.core.database import build_engine_for_connector
from app.modules.admin_catalog.models import DatabaseType
from app.modules.chat_engine.sql_executor import ReadOnlyTransactionError, SQLExecutor


class _FakeConn:
    def __init__(self, fail_set: bool = False):
        self.fail_set = fail_set
        self.statements = []

    def execute(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        if self.fail_set and "SET TRANSACTION READ ONLY" in sql:
            raise RuntimeError("el servidor no permite read-only")
        return SimpleNamespace(fetchall=lambda: [])


class TestReadOnlyEnforcement(unittest.TestCase):

    def test_connector_engine_starts_in_read_only_with_timeout(self):
        conn = SimpleNamespace(
            db_type=DatabaseType.POSTGRESQL,
            encrypted_password="",
            username="lector",
            host="db.example",
            port=5432,
            database_name="empresa",
        )
        with patch("app.core.database.create_engine", return_value=object()) as create:
            build_engine_for_connector(conn)

        connect_args = create.call_args.kwargs["connect_args"]
        self.assertIn("default_transaction_read_only=on", connect_args["options"])
        self.assertIn(
            f"statement_timeout={settings.QUERY_TIMEOUT_SECONDS * 1000}",
            connect_args["options"],
        )

    def test_explicit_write_engine_is_the_only_exception(self):
        """La remediacion de nulos pide `read_only=False` a proposito."""
        conn = SimpleNamespace(
            db_type=DatabaseType.POSTGRESQL,
            encrypted_password="",
            username="lector",
            host="db.example",
            port=5432,
            database_name="empresa",
        )
        with patch("app.core.database.create_engine", return_value=object()) as create:
            build_engine_for_connector(conn, read_only=False)

        options = create.call_args.kwargs["connect_args"]["options"]
        self.assertIn("default_transaction_read_only=off", options)

    def test_enforce_read_only_raises_when_set_fails(self):
        with self.assertRaises(ReadOnlyTransactionError):
            SQLExecutor._enforce_read_only(_FakeConn(fail_set=True))

    def test_execute_raw_sql_does_not_run_query_if_read_only_cannot_be_set(self):
        fake_conn = _FakeConn(fail_set=True)
        target_db = SimpleNamespace(db_type="postgresql")

        class _FakeEngine:
            def connect(self):
                @contextmanager
                def _cm():
                    yield fake_conn
                return _cm()

        @contextmanager
        def _fake_connector_engine(_conn):
            yield _FakeEngine()

        with patch("app.core.database.connector_engine", _fake_connector_engine):
            with self.assertRaises(ReadOnlyTransactionError):
                SQLExecutor.execute_raw_sql(target_db, "SELECT 1")

        self.assertFalse(
            any("SELECT 1" in statement for statement in fake_conn.statements),
            f"la consulta se ejecuto pese a no poder fijar read-only: {fake_conn.statements}",
        )


if __name__ == "__main__":
    unittest.main()

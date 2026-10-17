"""NullManagerService no puede afirmar una remediacion que no ocurrio.

Contexto: este servicio ESCRIBE en la base del cliente (borra filas, hace
UPDATE). Decir "aplicada con exito" sin haber modificado una sola celda hace
que el usuario crea que su base quedo remediada cuando no fue asi, y tratar
el texto 'None' como nulo hacia que `delete_rows` borrara filas de datos
legitimos.

Cada test reproduce un escenario concreto verificado contra el codigo real.
La fuente es una base PostgreSQL desechable: el servicio es PG-only (escribe
con `ctid`, `LAG() OVER`, etc.), asi que no hay rama SQLite que probar.
"""

import asyncio
import inspect as py_inspect
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from conftest import (
    create_temp_pg_database,
    drop_temp_pg_database,
    pg_source_conn_attrs,
    pg_source_engine,
)

from app.modules.catalog.services.null_manager import NullManagerService
from app.modules.chat_engine.engine import QueryEngine


class NullManagerTestCase(unittest.TestCase):
    """Base PostgreSQL desechable, vacia antes de cada test.

    El audit recorre TODAS las tablas del schema: si un test deja una tabla,
    el siguiente la audita y los conteos dejan de ser suyos. Por eso cada
    test arranca con el schema `public` limpio.
    """

    @classmethod
    def setUpClass(cls):
        cls.pg_db = create_temp_pg_database("nulls")

    @classmethod
    def tearDownClass(cls):
        drop_temp_pg_database(cls.pg_db)

    def setUp(self):
        self.db = MagicMock()
        self._drop_all_tables()

    # --- helpers ---------------------------------------------------------

    def _drop_all_tables(self):
        eng = pg_source_engine(self.pg_db)
        try:
            with eng.begin() as c:
                names = [r[0] for r in c.execute(text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                ))]
                for name in names:
                    c.execute(text(f'DROP TABLE IF EXISTS public."{name}" CASCADE'))
        finally:
            eng.dispose()

    def _exec(self, sql):
        eng = pg_source_engine(self.pg_db)
        try:
            with eng.begin() as c:
                c.execute(text(sql))
        finally:
            eng.dispose()

    def _rows(self, table):
        eng = pg_source_engine(self.pg_db)
        try:
            with eng.begin() as c:
                return [tuple(r) for r in c.execute(text(f'SELECT * FROM "{table}" ORDER BY 1'))]
        finally:
            eng.dispose()

    def _make_conn(self, db_name=None):
        conn = MagicMock()
        # Id inexistente + `is_uploaded=True`: el introspector filtra las tablas
        # por los permisos de la conexion subida, y con un id real (1) el filtro
        # dependeria del estado de la base de metadatos de tests.
        conn.id = 999999
        conn.is_uploaded = True
        for key, value in pg_source_conn_attrs(db_name or self.pg_db).items():
            setattr(conn, key, value)
        conn.null_policy = "open"
        conn.name = "test"
        return conn


class TestDeleteRowsDoesNotEatTheStringNone(NullManagerTestCase):
    """BUG 2: `WHERE col IS NULL OR LOWER(col) IN ('null','none')`.

    Una columna de texto con el valor real 'None' (un proveedor, un estado, una
    observacion) era contada como nulo, y con policy='delete_rows' se BORRABA la
    fila de ese cliente. El usuario pidio borrar nulos, no borrar cosas que se
    llamen None.
    """

    def setUp(self):
        super().setUp()
        self._exec("CREATE TABLE clientes (id INTEGER PRIMARY KEY, proveedor TEXT, estado TEXT)")
        self._exec("INSERT INTO clientes VALUES (1, 'None', 'activo')")
        self._exec("INSERT INTO clientes VALUES (2, 'Acme', 'activo')")
        self._exec("INSERT INTO clientes VALUES (3, 'None', 'inactivo')")

    def test_audit_does_not_count_string_none_as_null(self):
        conn = self._make_conn()
        audit = NullManagerService.audit_connection_nulls(conn, self.db)

        self.assertFalse(
            audit["has_nulls"],
            "una columna sin un solo NULL real fue reportada con nulos: el "
            "literal 'None' se esta tomando por SQL NULL",
        )
        self.assertTrue(audit["is_complete"])

    def test_delete_rows_keeps_rows_whose_value_is_the_string_none(self):
        conn = self._make_conn()
        result = NullManagerService.apply_null_policy(conn, "delete_rows", self.db)

        rows = self._rows("clientes")
        self.assertEqual(len(rows), 3, f"delete_rows borro filas con dato legitimo: {rows}")
        self.assertIn((1, "None", "activo"), rows, "la fila del cliente con proveedor 'None' fue borrada")
        self.assertEqual(result["status"], "no_op")
        self.assertEqual(result["rows_affected"], 0)

    def test_delete_rows_still_removes_real_nulls(self):
        """El default IS NULL tiene que seguir borrando lo que es nulo de verdad."""
        self._exec("INSERT INTO clientes VALUES (4, NULL, 'activo')")

        conn = self._make_conn()
        result = NullManagerService.apply_null_policy(conn, "delete_rows", self.db)

        rows = self._rows("clientes")
        self.assertEqual(len(rows), 3)
        self.assertNotIn((4, None, "activo"), rows)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["rows_affected"], 1)


class TestZeroRowsIsNotSuccess(NullManagerTestCase):
    """BUG 1: `status: "success"` incondicional en la linea de return.

    Columna 100% nulos + policy='mode' -> el SELECT de la moda no devuelve
    filas -> el `if` se saltea -> cero celdas modificadas -> "Remediacion de
    nulos aplicada con exito". Mismo resultado con 'nearest'.
    """

    def setUp(self):
        super().setUp()
        self._exec("CREATE TABLE solo_nulos (id INTEGER PRIMARY KEY, valor TEXT)")
        self._exec("INSERT INTO solo_nulos VALUES (1, NULL)")
        self._exec("INSERT INTO solo_nulos VALUES (2, NULL)")
        self._exec("INSERT INTO solo_nulos VALUES (3, NULL)")

    def test_mode_on_all_null_column_does_not_report_success(self):
        conn = self._make_conn()
        result = NullManagerService.apply_null_policy(conn, "mode", self.db)

        self.assertEqual(
            result["status"], "no_op",
            "una columna 100% nula no tiene moda: se modificaron 0 celdas y se reporto 'exito'",
        )
        self.assertEqual(result["rows_affected"], 0)
        self.assertIn("No se remedió nada", result["message"])

    def test_nearest_on_all_null_column_does_not_report_success(self):
        conn = self._make_conn()
        result = NullManagerService.apply_null_policy(conn, "nearest", self.db)

        self.assertEqual(result["status"], "no_op")
        self.assertEqual(result["rows_affected"], 0)

    def test_unreachable_database_does_not_report_success(self):
        """Si la base de destino no se puede abrir, no se declara exito: el
        error se propaga y la politica no queda persistida."""
        conn = self._make_conn("base_que_no_existe")
        audit_ok = {
            "has_nulls": True, "total_tables_with_nulls": 1,
            "total_unverifiable_columns": 0, "is_complete": True,
            "current_policy": "open",
            "tables": [{
                "table_name": "solo_nulos", "schema_name": "public", "total_rows": 3,
                "columns_with_nulls": [{"column_name": "valor", "data_type": "TEXT",
                                        "null_count": 3, "null_percentage": 100.0}],
                "total_null_columns": 1, "unverifiable_columns": [],
            }],
        }
        with patch.object(NullManagerService, "audit_connection_nulls", return_value=audit_ok):
            with self.assertRaises(OperationalError):
                NullManagerService.apply_null_policy(conn, "delete_rows", self.db)

        self.db.rollback.assert_called_once()
        self.db.commit.assert_not_called()

    def test_remediation_that_changes_cells_reports_the_count(self):
        """El camino feliz si debe decir success, y decir cuantas celdas toco."""
        self._exec("CREATE TABLE mixta (id INTEGER PRIMARY KEY, valor TEXT)")
        self._exec("INSERT INTO mixta VALUES (1, 'A')")
        self._exec("INSERT INTO mixta VALUES (2, NULL)")

        conn = self._make_conn()
        result = NullManagerService.apply_null_policy(conn, "mode", self.db)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["rows_affected"], 1)
        self.assertIn("1 celda(s)", result["message"])


class TestNearestUsesThePrecedingValue(NullManagerTestCase):
    """BUG 3: la rama Postgres hacia `SELECT ... LIMIT 1` sin ORDER BY.

    Eso devuelve una fila arbitraria (tipicamente la primera). Con
    salario=[100, NULL, 500] rellenaba 100 en vez del precedente 500. Ahora el
    forward-fill es real: LAG() sobre ctid.
    """

    def setUp(self):
        super().setUp()
        self._exec("CREATE TABLE salarios (id INTEGER PRIMARY KEY, salario INTEGER)")
        self._exec("INSERT INTO salarios VALUES (1, 100)")
        self._exec("INSERT INTO salarios VALUES (2, NULL)")
        self._exec("INSERT INTO salarios VALUES (3, 500)")

    def test_forward_fill_takes_previous_value(self):
        conn = self._make_conn()
        result = NullManagerService.apply_null_policy(conn, "nearest", self.db)

        rows = self._rows("salarios")
        self.assertEqual(result["status"], "success")
        self.assertEqual(rows[1][1], 100, f"el forward-fill no tomo el valor precedente: {rows}")

    def test_nearest_sql_uses_lag_over_ctid_not_bare_limit_1(self):
        """El forward-fill tiene que pedir el predecessor, no una fila arbitraria."""
        src = py_inspect.getsource(NullManagerService.apply_null_policy)

        self.assertIn("LAG(", src, "no usa LAG(): no hay forward-fill real")
        self.assertIn("ORDER BY ctid", src)
        self.assertNotIn(
            "IS NOT NULL LIMIT 1'", src,
            "quedo el `LIMIT 1` sin ORDER BY que devuelve una fila arbitraria",
        )

    def test_forward_fill_updates_only_rows_that_were_null(self):
        """La CTE del forward-fill no debe pisar filas que ya tenian valor."""
        src = py_inspect.getsource(NullManagerService.apply_null_policy)

        self.assertIn(
            'AND t."{c_name}" IS NULL', src,
            "el UPDATE de forward-fill debe filtrar por IS NULL: si no, pisa datos validos",
        )


class TestScanMarksUnverifiableColumns(NullManagerTestCase):
    """BUG 4: `except Exception: pass` en el escaneo.

    GRANT de tabla pero no de columna -> el COUNT(*) lanza -> la columna se
    omite en silencio -> has_nulls=False -> "Base de datos limpia: No se
    detectaron valores nulos". Y peor: `delete_rows` borraba filas basandose en
    un conteo incompleto.
    """

    def setUp(self):
        super().setUp()
        self._exec("CREATE TABLE permisos (id INTEGER PRIMARY KEY, secreto TEXT, visible TEXT)")
        self._exec("INSERT INTO permisos VALUES (1, 'valor', 'ok')")
        self._exec("INSERT INTO permisos VALUES (2, NULL, NULL)")

    def test_column_scan_error_is_reported_as_unverifiable_not_clean(self):
        conn = self._make_conn()

        from sqlalchemy.engine import Connection as SAConnection
        real_execute = SAConnection.execute

        def flaky_execute(self, statement, *args, **kwargs):
            """La conexion real, salvo la columna restringida: revienta como el
            servidor cuando hay GRANT de tabla pero no de columna."""
            if "secreto" in str(statement):
                raise RuntimeError("permission denied for column secreto")
            return real_execute(self, statement, *args, **kwargs)

        with patch.object(SAConnection, "execute", flaky_execute):
            audit = NullManagerService.audit_connection_nulls(conn, self.db)

        self.assertFalse(
            audit["is_complete"],
            "una columna que no se pudo escanear fue reportada como escaneo completo",
        )
        self.assertEqual(audit["total_unverifiable_columns"], 1)
        self.assertTrue(
            audit["has_nulls"],
            "la columna 'visible' tambien tiene un NULL real, asi que has_nulls debe ser True",
        )
        tbl = audit["tables"][0]
        unverifiable = [c["column_name"] for c in tbl["unverifiable_columns"]]
        self.assertIn("secreto", unverifiable)
        self.assertIn("permission denied", tbl["unverifiable_columns"][0]["reason"])

    def test_apply_policy_warns_instead_of_claiming_a_clean_database(self):
        """`no hay nulos` + escaneo incompleto no puede llamarse 'base limpia'."""
        with patch.object(
            NullManagerService, "audit_connection_nulls",
            return_value={
                "has_nulls": False, "total_tables_with_nulls": 1,
                "tables": [], "total_unverifiable_columns": 3,
                "is_complete": False, "current_policy": "open",
            },
        ):
            conn = self._make_conn()
            result = NullManagerService.apply_null_policy(conn, "delete_rows", self.db)

        self.assertEqual(result["status"], "no_op")
        self.assertIn("INCOMPLETO", result["message"])
        self.assertIn("permisos de columna", result["message"])


class TestPolicyIsNotPersistedBeforeItIsApplied(NullManagerTestCase):
    """BUG 5: `conn.null_policy = policy; db.commit()` iba ANTES del audit y de
    la aplicacion. Si el audit o la remediacion fallaban, la conexion quedaba
    diciendo que la politica estaba activa sobre una base sin remediar, y el
    frontend la mostraba como aplicada.
    """

    def setUp(self):
        super().setUp()
        self._exec("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        self._exec("INSERT INTO t VALUES (1, NULL)")

    def test_audit_failure_rolls_back_the_policy(self):
        conn = self._make_conn()
        with patch.object(
            NullManagerService, "audit_connection_nulls",
            side_effect=RuntimeError("no se pudo abrir la base"),
        ):
            with self.assertRaises(RuntimeError):
                NullManagerService.apply_null_policy(conn, "delete_rows", self.db)

        self.db.commit.assert_not_called()
        self.db.rollback.assert_called_once()

    def test_remediation_failure_rolls_back_the_policy(self):
        conn = self._make_conn("base_que_no_existe")
        audit_ok = {
            "has_nulls": True, "total_tables_with_nulls": 1,
            "total_unverifiable_columns": 0, "is_complete": True, "current_policy": "open",
            "tables": [{
                "table_name": "t", "schema_name": "public", "total_rows": 1,
                "columns_with_nulls": [{"column_name": "v", "data_type": "TEXT",
                                        "null_count": 1, "null_percentage": 100.0}],
                "total_null_columns": 1, "unverifiable_columns": [],
            }],
        }
        with patch.object(NullManagerService, "audit_connection_nulls", return_value=audit_ok):
            with self.assertRaises(OperationalError):
                NullManagerService.apply_null_policy(conn, "delete_rows", self.db)

        self.db.commit.assert_not_called()
        self.db.rollback.assert_called_once()

    def test_success_commits_exactly_once_at_the_end(self):
        self._exec("INSERT INTO t VALUES (2, 'X')")
        conn = self._make_conn()
        result = NullManagerService.apply_null_policy(conn, "mode", self.db)

        self.assertEqual(result["status"], "success")
        self.db.commit.assert_called_once()


class _NullDbFixture(NullManagerTestCase):
    """Base PostgreSQL desechable con `test_sales`, para el flujo del chat.

    `engine.execute_query` descartaba el retorno de `NullManagerService.apply_null_policy`.
    El servicio ya devuelve `status: "no_op"` / `rows_affected: 0` cuando no
    remedio nada, pero el chat miraba el exito viejo: el usuario clickeaba
    "eliminar nulos", la base no se tocaba y la respuesta decia
    "Tratamiento de nulos aplicado".
    """

    def setUp(self):
        super().setUp()
        self._exec("CREATE TABLE test_sales (id INTEGER PRIMARY KEY, category TEXT, amount REAL)")
        self._exec(
            "INSERT INTO test_sales VALUES "
            "(1, 'Electronics', 100.0), (2, NULL, 200.0), (3, 'Books', 50.0)"
        )

    def _mock_db_with(self, conn):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = conn
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = conn
        mock_db.query.return_value.order_by.return_value.first.return_value = conn
        return mock_db

    def _run_chat(self, mock_conn):
        """Ejecuta el flujo de remediacion del chat con el LLM apagado.

        Lo que este test verifica es el AVISO de remediacion, no la narrativa:
        sin el mock cada test espera los timeouts reales del LLM (~23s) y el
        resultado depende de si hay un modelo levantado. Con el LLM caido el
        motor tiene que seguir funcionando y diciendo lo mismo.
        """
        with patch(
            "app.modules.chat_engine.llm_service.LLMService.generate_completion",
            new=AsyncMock(return_value=None),
        ):
            return asyncio.run(QueryEngine.execute_query(
                question="Tratar nulos en test_sales (eliminar registros con nulos) para la consulta: SELECT * FROM test_sales",
                user_role="Administrador",
                is_admin=True,
                db=self._mock_db_with(mock_conn),
                connection_id=1,
                conversation_history=[
                    {"question": "SELECT * FROM test_sales", "sql": "SELECT * FROM test_sales"}
                ],
            ))

    def _chat(self):
        return self._run_chat(self._make_conn())


class TestChatReportsNullRemediationResult(_NullDbFixture):
    """Lo que el CHAT dice tras remediar: tiene que calcar lo que el servicio reporto."""

    def test_no_op_is_communicated_instead_of_claiming_success(self):
        no_op = {
            "status": "no_op",
            "policy": "delete_rows",
            "rows_affected": 0,
            "message": "No se detectaron valores nulos: no había nada que remediar.",
        }
        with patch("app.modules.chat_engine.engine.NullManagerService.apply_null_policy", return_value=no_op):
            response = self._chat()

        text = response.conversational_response
        self.assertIn("No se remedió ningún valor nulo", text)
        self.assertIn("no había nada que remediar", text)
        self.assertNotIn(
            "Tratamiento de nulos",
            text,
            "el banner de exito no puede seguir apareciendo cuando no se remedio nada",
        )

    def test_zero_rows_with_success_status_is_also_not_claimed_as_applied(self):
        """El `status` viejo ya daba success con 0 filas: el aviso no depende solo de el."""
        with patch(
            "app.modules.chat_engine.engine.NullManagerService.apply_null_policy",
            return_value={"status": "success", "policy": "mode", "rows_affected": 0, "message": "0 celdas."},
        ):
            response = self._chat()

        self.assertIn("No se remedió ningún valor nulo", response.conversational_response)
        self.assertNotIn("Tratamiento de nulos", response.conversational_response)

    def test_remediation_with_rows_affected_is_reported_as_applied(self):
        applied = {
            "status": "success",
            "policy": "delete_rows",
            "rows_affected": 2,
            "message": "Remediación de nulos aplicada bajo la política 'delete_rows': 2 celda(s) modificada(s).",
        }
        with patch("app.modules.chat_engine.engine.NullManagerService.apply_null_policy", return_value=applied):
            response = self._chat()

        text = response.conversational_response
        self.assertIn("Tratamiento de nulos", text)
        self.assertIn("Eliminación de registros con nulos", text)
        self.assertNotIn("No se remedió ningún valor nulo", text)

    def test_policy_is_still_forced_to_open_afterwards(self):
        """El aviso no puede cambiar la politica aplicada: sigue siendo 'open'."""
        no_op = {"status": "no_op", "policy": "delete_rows", "rows_affected": 0, "message": "nada que remediar"}
        mock_conn = self._make_conn()
        mock_conn.null_policy = "delete_rows"

        with patch("app.modules.chat_engine.engine.NullManagerService.apply_null_policy", return_value=no_op):
            self._run_chat(mock_conn)

        self.assertEqual(mock_conn.null_policy, "open")


if __name__ == "__main__":
    unittest.main()

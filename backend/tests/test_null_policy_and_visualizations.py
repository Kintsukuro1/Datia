"""Politica de nulos y visualizaciones del motor, sobre una base PostgreSQL real.

El audit y la remediacion ESCRIBEN/LEEN la base del cliente: la fuente es una
base desechable, no un mock, porque lo que se verifica es el efecto real
(filas borradas, celdas imputadas) y no que se haya llamado al servicio.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import text

from conftest import (
    create_temp_pg_database,
    drop_temp_pg_database,
    pg_source_conn_attrs,
    pg_source_engine,
)

from app.modules.catalog.services.null_manager import NullManagerService
from app.modules.chat_engine.kpi_calculator import KPICalculator
from app.modules.chat_engine.engine import QueryEngine


class TestNullPolicyAndVisualizations(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.pg_db = create_temp_pg_database("nullvis")

    @classmethod
    def tearDownClass(cls):
        drop_temp_pg_database(cls.pg_db)

    def setUp(self):
        # Cada test arranca con la base limpia: el audit recorre TODAS las
        # tablas del schema y las que dejo el test anterior contaminarian los
        # conteos.
        eng = pg_source_engine(self.pg_db)
        try:
            with eng.begin() as c:
                c.execute(text("DROP TABLE IF EXISTS test_sales"))
                c.execute(text("""
                    CREATE TABLE test_sales (
                        id INTEGER PRIMARY KEY,
                        category TEXT,
                        amount REAL,
                        region TEXT
                    )
                """))
                # 5 filas: 2 con nulos, 3 limpias.
                # Fila 2: category NULL | Fila 3: amount NULL
                c.execute(text(
                    "INSERT INTO test_sales VALUES "
                    "(1, 'Electronics', 100.0, 'North'),"
                    "(2, NULL, 200.0, 'North'),"
                    "(3, 'Electronics', NULL, 'South'),"
                    "(4, 'Electronics', 100.0, 'North'),"
                    "(5, 'Books', 50.0, 'South')"
                ))
        finally:
            eng.dispose()

    # --- helpers ---------------------------------------------------------

    def _query(self, sql):
        eng = pg_source_engine(self.pg_db)
        try:
            with eng.begin() as c:
                return [tuple(r) for r in c.execute(text(sql))]
        finally:
            eng.dispose()

    def _make_conn(self, null_policy="open"):
        mock_conn = MagicMock()
        # Id inexistente + `is_uploaded=True`: el introspector filtra por los
        # permisos de la conexion subida, y con un id real el filtro dependeria
        # del estado de la base de metadatos de tests.
        mock_conn.id = 999999
        mock_conn.is_uploaded = True
        for key, value in pg_source_conn_attrs(self.pg_db).items():
            setattr(mock_conn, key, value)
        mock_conn.null_policy = null_policy
        mock_conn.name = "test"
        return mock_conn

    def _mock_db_with(self, conn):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = conn
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = conn
        mock_db.query.return_value.order_by.return_value.first.return_value = conn
        return mock_db

    def _run_query(self, question, conn, conversation_history=None):
        """Corre el motor con el LLM apagado.

        Lo que se verifica es comportamiento del motor (pausa por nulos, filas
        limpias, banner), no el texto de la narrativa: sin el mock cada test
        espera los timeouts reales del LLM y depende de si hay un modelo
        levantado.
        """
        with patch(
            "app.modules.chat_engine.llm_service.LLMService.generate_completion",
            new=AsyncMock(return_value=None),
        ):
            return asyncio.run(QueryEngine.execute_query(
                question=question,
                user_role="Administrador",
                is_admin=True,
                db=self._mock_db_with(conn),
                connection_id=1,
                conversation_history=conversation_history,
            ))

    # --- audit / remediacion ---------------------------------------------

    def test_audit_nulls(self):
        """Verifica que el scanner detecte con precisión tablas, columnas y celdas con NULL."""
        audit = NullManagerService.audit_connection_nulls(self._make_conn())

        self.assertTrue(audit["has_nulls"])
        self.assertEqual(len(audit["tables"]), 1)

        table_info = audit["tables"][0]
        self.assertEqual(table_info["table_name"], "test_sales")
        self.assertEqual(table_info["total_rows"], 5)

        # Columns with nulls: category (1), amount (1)
        col_names = [c["column_name"] for c in table_info["columns_with_nulls"]]
        self.assertIn("category", col_names)
        self.assertIn("amount", col_names)

    def test_apply_policy_delete_rows(self):
        """Verifica que la política 'delete_rows' elimine las filas con NULL preservando las demás."""
        result = NullManagerService.apply_null_policy(self._make_conn(), "delete_rows", MagicMock(), confirm=True)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["policy"], "delete_rows")

        # Rows 2 and 3 were deleted, remaining 3 rows
        count = self._query("SELECT COUNT(*) FROM test_sales")[0][0]
        self.assertEqual(count, 3)

    def test_apply_policy_mode(self):
        """Verifica que la política 'mode' impute los NULLs con la moda estadística."""
        result = NullManagerService.apply_null_policy(self._make_conn(), "mode", MagicMock(), confirm=True)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["policy"], "mode")

        null_count = self._query(
            "SELECT COUNT(*) FROM test_sales WHERE category IS NULL OR amount IS NULL"
        )[0][0]
        row2_cat = self._query("SELECT category FROM test_sales WHERE id = 2")[0][0]

        self.assertEqual(null_count, 0)
        self.assertEqual(row2_cat, "Electronics")

    def test_apply_policy_nearest(self):
        """Verifica que la política 'nearest' impute los NULLs con el valor anterior/siguiente."""
        result = NullManagerService.apply_null_policy(self._make_conn(), "nearest", MagicMock(), confirm=True)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["policy"], "nearest")

        null_count = self._query(
            "SELECT COUNT(*) FROM test_sales WHERE category IS NULL OR amount IS NULL"
        )[0][0]
        self.assertEqual(null_count, 0)

    # --- visualizaciones / motor -----------------------------------------

    def test_dynamic_chart_series_generation(self):
        """Verifica que build_dynamic_visualization genere series ECharts pobladas y válidas."""
        columns = ["categoria", "ventas"]
        rows = [
            {"categoria": "Tecnología", "ventas": 50000},
            {"categoria": "Hogar", "ventas": 32000},
            {"categoria": "Moda", "ventas": 18000},
        ]

        kpis, chart_type, chart_option, summary, exec_rep = KPICalculator.build_dynamic_visualization(
            question="Ventas por categoría",
            columns=columns,
            rows=rows,
            user_role="Gerente"
        )

        self.assertIn("series", chart_option)
        self.assertGreater(len(chart_option["series"]), 0)
        first_series = chart_option["series"][0]
        self.assertIn("data", first_series)
        self.assertGreater(len(first_series["data"]), 0)

    def test_in_memory_null_remediation(self):
        """Verifica que el motor remedie nulos en memoria cuando el usuario lo solicita."""
        sample_rows = [
            {"id": 1, "producto": "Laptop", "precio": 1000},
            {"id": 2, "producto": None, "precio": 500},
            {"id": 3, "producto": "Mouse", "precio": None},
        ]
        columns = ["id", "producto", "precio"]

        # 1. delete_rows
        cleaned_del = QueryEngine._apply_in_memory_null_remediation(sample_rows, columns, "delete_rows")
        self.assertEqual(len(cleaned_del), 1)
        self.assertEqual(cleaned_del[0]["producto"], "Laptop")

        # 2. mode
        cleaned_mode = QueryEngine._apply_in_memory_null_remediation(sample_rows, columns, "mode")
        self.assertEqual(len(cleaned_mode), 3)
        self.assertIsNotNone(cleaned_mode[1]["producto"])

    def test_prompt1_early_null_pause(self):
        """Verifica que el Prompt 1 con nulos y política 'open' pause la respuesta sin mostrar gráficos ni KPIs distorsionados."""
        conn = self._make_conn(null_policy="open")
        response = self._run_query("SELECT * FROM test_sales", conn)

        # 1. Null detection was triggered
        self.assertIsNotNone(response.nulls_detected)
        self.assertTrue(response.nulls_detected["has_nulls"])
        self.assertEqual(response.nulls_detected["null_rows_count"], 2)

        # 2. Distorted KPIs and charts are suppressed
        self.assertEqual(len(response.kpis), 0)
        self.assertEqual(response.chart_type, "none")
        self.assertEqual(len(response.chart_option.get("series", [])), 0)

        # 3. Conversational response warns about nulls and asks for remediation
        self.assertIn("Valores nulos detectados", response.conversational_response)
        self.assertIn("test_sales", response.conversational_response)

        # 4. Options embed the original question for context preservation
        options = response.nulls_detected["options"]
        self.assertEqual(len(options), 3)
        self.assertIn("SELECT * FROM test_sales", options[0]["prompt"])

    def test_prompt2_remediation_answers_original_question(self):
        """Verifica que el Prompt 2 reciba la acción de remediación, conserve el contexto de la consulta original y devuelva los datos limpios."""
        conn = self._make_conn(null_policy="open")

        # Simulate user clicking "Eliminar registros con nulos"
        remediation_prompt = "Tratar nulos en test_sales (eliminar registros con nulos) para la consulta: SELECT * FROM test_sales"
        response = self._run_query(
            remediation_prompt,
            conn,
            conversation_history=[
                {"question": "SELECT * FROM test_sales", "sql": "SELECT * FROM test_sales"}
            ],
        )

        # 1. Did NOT pause again
        self.assertIsNone(response.nulls_detected)

        # 2. Clean data returned (no nulls)
        self.assertGreater(len(response.data_rows), 0)
        for r in response.data_rows:
            self.assertIsNotNone(r.get("category"))
            self.assertIsNotNone(r.get("amount"))

        # 3. Confirmation banner is present
        self.assertIn("Tratamiento de nulos", response.conversational_response)
        self.assertIn("Eliminación de registros con nulos", response.conversational_response)

        # 4. La base fisica quedo intacta: la remediacion fue SOLO en memoria.
        # Antes de plan 002 este flujo ejecutaba DELETE sobre test_sales y estas
        # dos aserciones fallaban (quedaban 3 filas y 0 nulos).
        self.assertEqual(
            self._query("SELECT COUNT(*) FROM test_sales")[0][0], 5,
            "el chat modifico la base corporativa en vez de remediar en memoria",
        )
        self.assertEqual(
            self._query(
                "SELECT COUNT(*) FROM test_sales WHERE category IS NULL OR amount IS NULL"
            )[0][0],
            2,
            "los nulos fisicos desaparecieron: se ejecuto una remediacion persistente",
        )

    def test_query_without_matching_rows_does_not_crash(self):
        """Un filtro que no matchea filas no crashea y no afirma que encontro datos.

        Este test NO puede afirmar la frase exacta del resumen: `summary_text` sale
        del LLM (`engine.py` toma el primer bloque de `conversational`), asi que
        la frase cambia entre corridas -- con un 7B local decimos "con el ID 99999"
        un dia y "con el ID proporcionado (99999)" al otro. Afirmar la cadena
        convertia un modelo no determinista en un test flaky PARA SIEMPRE, y peor:
        invitaba a tocar produccion para que el modelo dijera la frase, en vez de
        arreglar el test. Lo que se verifica aca es el comportamiento: cero filas,
        respuesta completa, y un resumen que no afirme un resultado.
        """
        conn = self._make_conn(null_policy="strict")
        response = self._run_query("SELECT * FROM test_sales WHERE id = 99999", conn)

        # 1. Cero filas, contadas por el motor y no por el texto.
        self.assertEqual(response.traceability.rows_returned, 0)
        self.assertEqual(response.data_rows, [])

        # 2. La respuesta esta completa y es parseable: no un None ni un string vacio.
        self.assertIsInstance(response.summary_text, str)
        self.assertTrue(response.summary_text.strip(), "el resumen quedo vacio")

        # 3. Y no declara exito sobre una consulta que no trajo nada. Esta es la
        #    garantia honesta del test, y no depende de como lo diga el modelo:
        #    "No se encontraron ...", "0 registros", "sin coincidencias" dan igual.
        self.assertNotIn("99999 filas", response.summary_text)
        for afirmacion in ("se encontraron 99999", "99999 resultados", "se obtuvieron 99999"):
            self.assertNotIn(afirmacion, response.summary_text.lower())

        # Contrato del retorno: siempre 5 elementos, con el KPI de cero registros.
        # Aca si se puede afirmar el valor exacto del KPI: lo produce
        # `KPICalculator`, que es codigo, no el modelo.
        kpis, chart_type, chart_option, summary, exec_rep = KPICalculator.build_dynamic_visualization(
            "ventas del cliente 99999", ["cliente", "monto"], []
        )
        self.assertEqual(len(kpis), 1)
        self.assertEqual(kpis[0].value, "0 Registros")
        self.assertEqual(chart_type, "bar")
        self.assertEqual(chart_option["series"], [], "un grafico sin datos no dibuja series")
        # El resumen determinista tiene que ser parseable y no una afirmacion de exito.
        self.assertIsInstance(summary, str)
        self.assertTrue(summary.strip())
        self.assertIsNotNone(exec_rep)
        self.assertTrue(
            any("0" in str(f) or "Cero" in str(f) for f in exec_rep.key_findings),
            f"el informe ejecutivo no registra que no hubo coincidencias: {exec_rep.key_findings}",
        )

    def test_conversational_branch_survives_non_ast_exception(self):
        """Una excepción no-AST en la rama conversacional no debe dejar secured_sql/meta sin definir."""
        from app.modules.chat_engine.ast_validator import ASTValidator
        from app.modules.chat_engine.intent_classifier import IntentClassifier
        from app.modules.chat_engine.sql_executor import SQLExecutor

        conn = self._make_conn(null_policy="strict")
        with patch.object(IntentClassifier, "classify_intent", new=AsyncMock(return_value="conversational")), \
             patch.object(SQLExecutor, "get_grounding_query", return_value="SELECT 1"), \
             patch.object(ASTValidator, "validate_and_secure_sql", side_effect=RuntimeError("sqlglot blew up")), \
             patch.object(IntentClassifier, "generate_conversational_response", new=AsyncMock(return_value="respuesta ok")):
            response = self._run_query("¿Cómo va el panorama de ventas?", conn)

        self.assertEqual(response.traceability.rows_returned, 0)
        self.assertEqual(response.conversational_response, "respuesta ok")


if __name__ == "__main__":
    unittest.main()

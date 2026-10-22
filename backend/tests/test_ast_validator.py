import unittest
from app.core.config import settings
from app.modules.chat_engine.ast_validator import ASTValidator, ASTValidationError

# El techo de filas lo decide `settings.DEFAULT_ROW_LIMIT`, no un 500 escrito
# aqui. Con el literal, estos tests affirmations una sola configuracion: en CI
# (sin `.env`) el default es 500 y pasaban, pero en una maquina con
# DEFAULT_ROW_LIMIT=1000 el clamp a 500 ya no ocurria y el fallo se reportaba
# como si fuera del codigo cuando era del entorno.
MAX = settings.DEFAULT_ROW_LIMIT

class TestASTValidator(unittest.TestCase):

    def test_valid_select_query(self):
        sql = "SELECT id, monto, fecha FROM fact_ventas WHERE fecha >= '2026-01-01'"
        is_valid, secured_sql, meta = ASTValidator.validate_and_secure_sql(
            sql,
            dialect="postgres",
            allowed_tables={"fact_ventas"}
        )
        self.assertTrue(is_valid)
        self.assertIn(f"LIMIT {MAX}", secured_sql)
        self.assertIn("fact_ventas", meta["tables_used"])

    def test_generate_sql_explanation(self):
        sql = "SELECT dim_clientes.nombre, SUM(fact_ventas.monto) FROM fact_ventas JOIN dim_clientes ON fact_ventas.cliente_id = dim_clientes.id WHERE fact_ventas.monto > 100 GROUP BY dim_clientes.nombre ORDER BY SUM(fact_ventas.monto) DESC LIMIT 10"
        explanation = ASTValidator.generate_sql_explanation(sql, dialect="postgresql")
        self.assertIn("fact_ventas", explanation)
        self.assertIn("dim_clientes", explanation)
        self.assertIn("suma", explanation.lower())
        self.assertIn("filtra por", explanation.lower())
        self.assertIn("agrupa por", explanation.lower())
        self.assertIn("10", explanation)

    def test_reject_dml_operation(self):
        sql = "DELETE FROM fact_ventas WHERE id = 1"
        with self.assertRaises(ASTValidationError) as excinfo:
            ASTValidator.validate_and_secure_sql(sql, allowed_tables={"fact_ventas"})
        self.assertIn("Únicamente se permiten consultas SELECT", str(excinfo.exception))

    def test_reject_semicolon_chaining(self):
        sql = "SELECT * FROM fact_ventas; DROP TABLE dim_clientes;"
        with self.assertRaises(ASTValidationError) as excinfo:
            ASTValidator.validate_and_secure_sql(sql, allowed_tables={"fact_ventas"})
        self.assertIn("Se prohíbe el encadenamiento", str(excinfo.exception))

    def test_reject_unauthorized_table(self):
        sql = "SELECT * FROM dim_empleados_rrhh"
        with self.assertRaises(ASTValidationError) as excinfo:
            ASTValidator.validate_and_secure_sql(sql, allowed_tables={"fact_ventas", "dim_productos"})
        self.assertIn("Gobernanza RBAC: Acceso denegado. No tienes permisos para acceder ni manejar estos datos.", str(excinfo.exception))

    def test_reject_blocked_column(self):
        sql = "SELECT id, salario_base FROM fact_ventas"
        with self.assertRaises(ASTValidationError) as excinfo:
            ASTValidator.validate_and_secure_sql(
                sql,
                allowed_tables={"fact_ventas"},
                blocked_columns={"salario_base"}
            )
        self.assertIn("Gobernanza RBAC: Acceso denegado. No tienes permisos para acceder ni manejar estos datos.", str(excinfo.exception))

    def test_cte_query_does_not_flag_cte_as_unauthorized_table(self):
        sql = "WITH metricas AS (SELECT id, monto FROM fact_ventas) SELECT id, monto FROM metricas"
        is_valid, secured, meta = ASTValidator.validate_and_secure_sql(
            sql,
            allowed_tables={"fact_ventas"}
        )
        self.assertTrue(is_valid)
        self.assertIn("fact_ventas", meta["tables_used"])
        self.assertNotIn("metricas", meta["tables_used"])

    def test_limit_clamp_contract(self):
        """El techo es un maximo, no un piso, y aplica a todo el arbol."""
        # sqlglot>=30: Literal.this es read-only. Limitar sobre el maximo no debe
        # lanzar AttributeError.
        for over in (MAX + 1, MAX * 1000):
            with self.subTest(case="sobre_maximo", limit=over):
                is_valid, secured_sql, _ = ASTValidator.validate_and_secure_sql(
                    f"SELECT id_venta FROM fact_ventas LIMIT {over}",
                    dialect="postgres", allowed_tables={"fact_ventas"}
                )
                self.assertTrue(is_valid)
                self.assertIn(f"LIMIT {MAX}", secured_sql)
                self.assertNotIn(f"LIMIT {over}", secured_sql)

        with self.subTest(case="en_el_maximo"):
            is_valid, secured_sql, _ = ASTValidator.validate_and_secure_sql(
                f"SELECT id_venta FROM fact_ventas LIMIT {MAX}",
                dialect="postgres", allowed_tables={"fact_ventas"}
            )
            self.assertTrue(is_valid)
            self.assertIn(f"LIMIT {MAX}", secured_sql)

        # El clamp es un techo, no un piso: LIMIT 5 debe seguir siendo 5.
        for literal in ("5", "1"):
            with self.subTest(case="limite_chico", limit=literal):
                is_valid, secured_sql, _ = ASTValidator.validate_and_secure_sql(
                    f"SELECT id_venta FROM fact_ventas LIMIT {literal}",
                    dialect="postgres", allowed_tables={"fact_ventas"}
                )
                self.assertTrue(is_valid)
                self.assertIn(f"LIMIT {literal}", secured_sql)
                self.assertNotIn(f"LIMIT {MAX}", secured_sql)

        with self.subTest(case="tsql_top"):
            # TOP es un Limit en el AST de tsql: tambien debe clampearse y no crashear.
            is_valid, secured_sql, _ = ASTValidator.validate_and_secure_sql(
                f"SELECT TOP {MAX * 1000} id FROM fact_ventas",
                dialect="tsql", allowed_tables={"fact_ventas"}
            )
            self.assertTrue(is_valid)
            self.assertIn(f"TOP {MAX}", secured_sql)
            # Sin esto el test pasa en vacio: "TOP 500" es subcadena de "TOP 500000".
            self.assertNotIn(f"TOP {MAX * 1000}", secured_sql)

        with self.subTest(case="subconsulta_sobre_maximo"):
            sql = (
                "SELECT COUNT(*) FROM (SELECT id_venta, monto FROM fact_ventas "
                f"LIMIT {MAX * 1000000}) x"
            )
            is_valid, secured_sql, _ = ASTValidator.validate_and_secure_sql(
                sql, dialect="postgres", allowed_tables={"fact_ventas"}
            )
            self.assertTrue(is_valid)
            self.assertNotIn(f"{MAX * 1000000}", secured_sql)
            self.assertIn(f"LIMIT {MAX}", secured_sql)

        with self.subTest(case="subconsulta_chica"):
            is_valid, secured_sql, _ = ASTValidator.validate_and_secure_sql(
                "SELECT id_venta FROM (SELECT id_venta FROM fact_ventas LIMIT 5) y",
                dialect="postgres", allowed_tables={"fact_ventas"}
            )
            self.assertTrue(is_valid)
            # `assertIn("LIMIT 5", ...)` no probaba nada: con MAX=500, "LIMIT 500"
            # CONTIENE "LIMIT 5". El `(?!\d)` obliga a que el 5 no sea el primer
            # digito de un numero mayor.
            self.assertRegex(secured_sql, r"LIMIT\s+5(?!\d)")
            self.assertIn(f"LIMIT {MAX}", secured_sql)

    def test_select_star_fails_closed_without_full_table_columns(self):
        """Sin columnas conocidas no se puede filtrar el star: falla cerrado."""
        for table_columns in (None, {}, {"otra_tabla": ["id"]}):
            with self.subTest(table_columns=table_columns):
                with self.assertRaises(ASTValidationError) as excinfo:
                    ASTValidator.validate_and_secure_sql(
                        "SELECT * FROM dim_clientes",
                        dialect="postgres",
                        allowed_tables={"dim_clientes"},
                        table_columns=table_columns
                    )
                if table_columns in (None, {}):
                    self.assertIn("SELECT *", str(excinfo.exception))

        # COUNT(*) no es una proyeccion star: no revela columnas y debe seguir funcionando.
        is_valid, secured_sql, _ = ASTValidator.validate_and_secure_sql(
            "SELECT COUNT(*) FROM dim_clientes",
            dialect="postgres",
            allowed_tables={"dim_clientes"}
        )
        self.assertTrue(is_valid)
        self.assertIn("COUNT(*)", secured_sql)

    def test_admin_bypasses_table_and_column_restrictions(self):
        sql = "SELECT id, salario_base FROM cualquier_tabla_corporativa"
        is_valid, secured, meta = ASTValidator.validate_and_secure_sql(
            sql,
            allowed_tables={"fact_ventas"},  # Table is outside allowed_tables
            blocked_columns={"salario_base"}, # Column is in blocked_columns
            is_admin=True
        )
        self.assertTrue(is_valid)
        self.assertIn("cualquier_tabla_corporativa", meta["tables_used"])

if __name__ == "__main__":
    unittest.main()

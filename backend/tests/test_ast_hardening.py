"""Endurecimiento del guardarrail AST: bypasses reproducidos y reglas de negocio.

Cada PoC de esta lista pasaba `validate_and_secure_sql` antes del fix:
lectura de archivos del servidor (`pg_read_file`, `pg_ls_dir`), fila completa
serializada (`row_to_json(t)`, `SELECT t`), tablas de otros esquemas
(`secreto.empleados`, `information_schema.tables`), `SELECT INTO`/`FOR UPDATE`,
`*` anidado fuera de `COUNT(*)` y el subscript JSONB `perfil['rut']`.
"""

import unittest

from app.modules.chat_engine.ast_validator import ASTValidator, ASTValidationError

EMPLOYEES = {"empleados"}
BLOCKED = {"sueldo"}
COLUMNS = {"empleados": ["id", "nombre", "sueldo"]}


class TestDangerousFunctionsAreRejected(unittest.TestCase):
    """Las funciones de sistema no estan en la allowlist y se rechazan."""

    def _assert_rejected(self, sql, **kwargs):
        with self.assertRaises(ASTValidationError) as ctx:
            ASTValidator.validate_and_secure_sql(
                sql, allowed_tables=EMPLOYEES, blocked_columns=BLOCKED, **kwargs
            )
        return str(ctx.exception)

    def test_system_functions_are_rejected(self):
        cases = (
            ("pg_read_file", "SELECT pg_read_file('pg_hba.conf') AS contenido", True),
            ("pg_ls_dir", "SELECT pg_ls_dir('.')", False),
            ("pg_sleep", "SELECT pg_sleep(10)", False),
            ("current_setting", "SELECT current_setting('data_directory')", False),
            ("set_config", "SELECT set_config('work_mem', '4GB', false)", False),
            ("lo_import", "SELECT lo_import('/etc/passwd')", False),
            ("query_to_xml", "SELECT query_to_xml('SELECT 1', true, false, '')", False),
            ("dblink", "SELECT dblink('host=127.0.0.1 dbname=postgres', 'SELECT 1')", False),
            ("pg_read_binary_file", "SELECT pg_read_binary_file('/etc/passwd')", False),
            ("pg_terminate_backend", "SELECT pg_terminate_backend(1)", False),
        )
        for name, sql, check_message in cases:
            with self.subTest(case=name):
                msg = self._assert_rejected(sql)
                if check_message:
                    self.assertIn("no permitida", msg.lower())

    def test_pg_read_file_is_rejected_even_for_admin(self):
        # El guardarrail de solo lectura no es una concesion de RBAC.
        with self.assertRaises(ASTValidationError):
            ASTValidator.validate_and_secure_sql(
                "SELECT pg_read_file('pg_hba.conf')", is_admin=True
            )

    def test_json_functions_and_extraction_are_rejected(self):
        cases = (
            ("row_to_json", "SELECT row_to_json(t) FROM empleados t", None),
            ("to_json", "SELECT to_json(nombre) FROM empleados", None),
            ("json_build_object", "SELECT json_build_object('clave', nombre) FROM empleados", None),
            ("jsonb_build_object", "SELECT jsonb_build_object('clave', nombre) FROM empleados", None),
            ("array_to_json", "SELECT array_to_json(ARRAY[nombre]) FROM empleados", None),
            ("json_agg", "SELECT json_agg(nombre) FROM empleados", None),
            # `perfil->>'rut'` no menciona la columna MASKED en un Column: el AST
            # solo veria `perfil` y el sink no puede enmascarar el escalar extraido.
            ("flecha", "SELECT perfil->'rut' AS x FROM empleados", None),
            ("flecha_texto", "SELECT perfil->>'rut' AS x FROM empleados", None),
            ("hash", "SELECT perfil#>'{a,rut}' AS x FROM empleados", None),
            ("hash_texto", "SELECT perfil#>>'{a,rut}' AS x FROM empleados", None),
            # PostgreSQL 14+ ejecuta `perfil['rut']` como extraccion JSONB. sqlglot
            # lo representa como `Bracket`, no como `JSONExtract`, asi que el AST
            # solo veia `perfil` y el sink no enmascaraba el escalar extraido.
            ("bracket", "SELECT perfil['rut'] AS x FROM empleados", "subíndice"),
            ("bracket_anidado", "SELECT perfil['a']['rut'] AS x FROM empleados", "subíndice"),
            ("bracket_indice", "SELECT perfil[1] AS x FROM empleados", "subíndice"),
        )
        for name, sql, expected in cases:
            with self.subTest(case=name):
                msg = self._assert_rejected(sql)
                if expected:
                    self.assertIn(expected, msg.lower())

    def test_schema_qualified_function_is_rejected(self):
        msg = self._assert_rejected("SELECT pg_catalog.pg_read_file('pg_hba.conf') AS contenido")
        self.assertIn("calificada", msg.lower())

        # `myfunc` no esta en la allowlist y ademas viene calificada: el Dot
        # tambien tiene que rechazarla aunque el nombre suelto pareciera inocuo.
        with self.assertRaises(ASTValidationError):
            ASTValidator.validate_and_secure_sql("SELECT myschema.myfunc(nombre) FROM empleados")


class TestTableReferencesAreRestricted(unittest.TestCase):

    def test_other_and_system_schemas_are_rejected(self):
        with self.assertRaises(ASTValidationError) as ctx:
            ASTValidator.validate_and_secure_sql(
                "SELECT id FROM secreto.empleados", allowed_tables=EMPLOYEES
            )
        self.assertIn("esquemas", str(ctx.exception).lower())

        with self.assertRaises(ASTValidationError):
            ASTValidator.validate_and_secure_sql("SELECT * FROM information_schema.tables")

    def test_public_schema_is_allowed(self):
        ok, secured, meta = ASTValidator.validate_and_secure_sql(
            "SELECT id, nombre FROM public.empleados",
            allowed_tables=EMPLOYEES,
            blocked_columns=BLOCKED,
        )
        self.assertTrue(ok)
        self.assertIn("empleados", meta["tables_used"])

    def test_public_schema_star_expansion_still_filters_blocked_columns(self):
        ok, secured, _ = ASTValidator.validate_and_secure_sql(
            "SELECT * FROM public.empleados",
            allowed_tables=EMPLOYEES,
            blocked_columns=BLOCKED,
            table_columns=COLUMNS,
        )
        self.assertTrue(ok)
        self.assertIn("id", secured.lower())
        self.assertNotIn("sueldo", secured.lower())

    def test_function_in_from_is_rejected(self):
        for sql in (
            "SELECT * FROM generate_series(1, 10)",
            "SELECT * FROM explode(array(1, 2))",
        ):
            with self.assertRaises(ASTValidationError):
                ASTValidator.validate_and_secure_sql(sql, allowed_tables=EMPLOYEES)


class TestWholeRowReferencesAreRejected(unittest.TestCase):

    def test_bare_whole_row_references_are_rejected(self):
        cases = (
            ("alias", "SELECT t FROM empleados t", True),
            ("campo_de_alias", "SELECT (t).sueldo FROM empleados t", False),
            ("nombre_de_tabla", "SELECT empleados FROM empleados", False),
        )
        for name, sql, check_message in cases:
            with self.subTest(case=name):
                with self.assertRaises(ASTValidationError) as ctx:
                    ASTValidator.validate_and_secure_sql(
                        sql, allowed_tables=EMPLOYEES
                    )
                if check_message:
                    self.assertIn("fila completa", str(ctx.exception).lower())

    def test_whole_row_cast_and_order_by_are_rejected(self):
        # `WHERE t::text LIKE ...` no proyecta la fila, pero permite inferir por
        # fuerza bruta el valor de una columna bloqueada.
        cases = (
            "SELECT id FROM empleados t WHERE t::text LIKE '%1234%'",
            "SELECT id FROM empleados t ORDER BY t",
        )
        for sql in cases:
            with self.subTest(sql=sql):
                with self.assertRaises(ASTValidationError):
                    ASTValidator.validate_and_secure_sql(
                        sql,
                        allowed_tables=EMPLOYEES,
                        blocked_columns=BLOCKED,
                    )

    def test_qualified_whole_row_is_rejected(self):
        """El calificador no alcanza: `public.empleados` y `t.t` son fila completa."""
        cases = (
            "SELECT public.empleados FROM public.empleados",
            "SELECT empleados.empleados FROM empleados",
            "SELECT t.t FROM empleados t",
            "SELECT t.empleados FROM empleados t",
        )
        for sql in cases:
            with self.subTest(sql=sql):
                with self.assertRaises(ASTValidationError):
                    ASTValidator.validate_and_secure_sql(
                        sql,
                        allowed_tables=EMPLOYEES,
                        blocked_columns=BLOCKED,
                        table_columns=COLUMNS,
                    )

    def test_qualified_column_from_another_table_is_not_whole_row(self):
        """`x.empleados` es una columna normal si `x` no es alias de `empleados`."""
        _, secured, _ = ASTValidator.validate_and_secure_sql(
            "SELECT x.empleados FROM clientes x JOIN empleados ON x.id = empleados.id",
            allowed_tables={"clientes", "empleados"},
            blocked_columns=BLOCKED,
            table_columns={
                "clientes": ["id", "empleados"],
                "empleados": ["id", "nombre", "sueldo"],
            },
        )
        self.assertIn("x.empleados", secured)

    def test_qualified_columns_are_allowed(self):
        ok, secured, _ = ASTValidator.validate_and_secure_sql(
            "SELECT t.id, t.nombre FROM empleados t",
            allowed_tables=EMPLOYEES,
            blocked_columns=BLOCKED,
        )
        self.assertTrue(ok)
        self.assertIn("t.id", secured)

    def test_whole_row_reference_over_cte_is_rejected(self):
        """Una CTE también es una fila completa si se proyecta sin columnas."""
        cases = (
            "WITH recientes AS (SELECT id FROM empleados) SELECT recientes FROM recientes",
            "WITH recientes AS (SELECT id FROM empleados) SELECT r.recientes FROM recientes r",
        )
        for sql in cases:
            with self.subTest(sql=sql):
                with self.assertRaises(ASTValidationError) as ctx:
                    ASTValidator.validate_and_secure_sql(
                        sql, allowed_tables=EMPLOYEES
                    )
                self.assertIn("fila completa", str(ctx.exception).lower())

    def test_whole_row_reference_over_derived_table_is_rejected(self):
        """`FROM (SELECT ...) d` también es una fila completa y no es exp.Table."""
        cases = (
            "SELECT d FROM (SELECT id FROM empleados) d",
            # Columna MASKED, no bloqueada: el dato saldría compuesto y sin
            # pasar por mask_query_rows porque la clave externa es `d`.
            "SELECT d FROM (SELECT rut_dni_cliente FROM dim_clientes) d",
            # La lista de alias renombra la salida a x, y; `d` ya no es una
            # columna de la derivada y vuelve a ser la fila completa.
            "SELECT d FROM (SELECT nombre AS d, rut_dni_cliente FROM dim_clientes) d(x, y)",
            # VALUES directo: `d` es el tuple de la fila, no una columna.
            "SELECT d FROM (VALUES ((SELECT perfil, nombre FROM dim_clientes LIMIT 1))) d",
        )
        for sql in cases:
            with self.subTest(sql=sql):
                with self.assertRaises(ASTValidationError) as ctx:
                    ASTValidator.validate_and_secure_sql(
                        sql,
                        allowed_tables={"empleados", "dim_clientes"},
                        blocked_columns=set(),
                        table_columns=COLUMNS,
                    )
                self.assertIn("fila completa", str(ctx.exception).lower())

    def test_derived_alias_list_column_is_not_whole_row(self):
        """Si `d` está en la lista de alias, es una columna, no la fila entera."""
        ok, _, _ = ASTValidator.validate_and_secure_sql(
            "SELECT d FROM (SELECT nombre AS d, rut_dni_cliente "
            "FROM dim_clientes) d(d, y)",
            allowed_tables={"dim_clientes"},
            blocked_columns=set(),
        )
        self.assertTrue(ok)

    def test_explicit_columns_from_derived_table_are_allowed(self):
        ok, secured, _ = ASTValidator.validate_and_secure_sql(
            "SELECT d.id FROM (SELECT id FROM empleados) d",
            allowed_tables=EMPLOYEES,
            blocked_columns=BLOCKED,
        )
        self.assertTrue(ok)
        self.assertIn("d.id", secured)

    def test_whole_row_reference_over_lateral_is_rejected(self):
        """LATERAL deja la subquery bajo exp.Lateral, no bajo Join."""
        with self.assertRaises(ASTValidationError) as ctx:
            ASTValidator.validate_and_secure_sql(
                "SELECT d FROM empleados e, "
                "LATERAL (SELECT rut_dni_cliente FROM dim_clientes) d",
                allowed_tables={"empleados", "dim_clientes"},
                blocked_columns=set(),
            )
        self.assertIn("fila completa", str(ctx.exception).lower())

    def test_derived_column_with_same_name_as_alias_is_not_whole_row(self):
        """`SELECT *` expande `id AS d`; ese `d` es columna, no la fila entera."""
        ok, secured, _ = ASTValidator.validate_and_secure_sql(
            "SELECT * FROM (SELECT id AS d FROM empleados) d",
            allowed_tables=EMPLOYEES,
            blocked_columns=BLOCKED,
            table_columns=COLUMNS,
        )
        self.assertTrue(ok)
        self.assertTrue(secured.lower().startswith("select d"))

    def test_derived_alias_in_another_subquery_does_not_reject_outer_column(self):
        """Un alias `d` dentro del WHERE no es visible en el SELECT externo."""
        ok, _, _ = ASTValidator.validate_and_secure_sql(
            "SELECT d FROM tabla1 WHERE id IN "
            "(SELECT id FROM (SELECT id FROM tabla2) d)",
            allowed_tables={"tabla1", "tabla2"},
            blocked_columns=set(),
            table_columns={"tabla1": ["id", "d"], "tabla2": ["id"]},
        )
        self.assertTrue(ok)


class TestCteCannotShadowPhysicalTables(unittest.TestCase):
    """Una CTE con el nombre de una tabla prohibida no la vuelve invisible.

    El codigo restaba todos los nombres de CTE del conjunto de tablas físicas.
    `WITH secreto AS (SELECT * FROM public.secreto) SELECT * FROM secreto`
    dejaba "secreto" fuera del control RBAC y leia la tabla real.
    """

    def test_cte_with_same_name_as_forbidden_table_is_rejected(self):
        cases = (
            "WITH secreto AS (SELECT id FROM public.secreto) SELECT id FROM secreto",
            "WITH empleados AS (SELECT id FROM public.secreto) SELECT id FROM empleados",
            "WITH secreto AS (SELECT id FROM secreto) SELECT id FROM secreto",
        )
        for sql in cases:
            with self.subTest(sql=sql):
                with self.assertRaises(ASTValidationError) as ctx:
                    ASTValidator.validate_and_secure_sql(
                        sql, allowed_tables=EMPLOYEES
                    )
                self.assertIn("secreto", str(ctx.exception).lower())

    def test_legitimate_cte_is_still_allowed(self):
        ok, _, meta = ASTValidator.validate_and_secure_sql(
            "WITH metricas AS (SELECT id FROM empleados) SELECT id FROM metricas",
            allowed_tables=EMPLOYEES,
        )
        self.assertTrue(ok)
        self.assertEqual(meta["tables_used"], ["empleados"])


class TestNestedStarIsRejected(unittest.TestCase):

    def test_row_constructor_with_star_is_rejected(self):
        with self.assertRaises(ASTValidationError):
            ASTValidator.validate_and_secure_sql(
                "SELECT ROW(t.*) FROM empleados t",
                allowed_tables=EMPLOYEES,
                blocked_columns=BLOCKED,
                table_columns=COLUMNS,
            )

    def test_count_star_is_still_allowed(self):
        ok, secured, _ = ASTValidator.validate_and_secure_sql(
            "SELECT COUNT(*) FROM empleados", allowed_tables=EMPLOYEES
        )
        self.assertTrue(ok)
        self.assertIn("COUNT(*)", secured)

    def test_projection_star_expansion_still_works(self):
        ok, secured, _ = ASTValidator.validate_and_secure_sql(
            "SELECT * FROM empleados",
            allowed_tables=EMPLOYEES,
            blocked_columns=BLOCKED,
            table_columns=COLUMNS,
        )
        self.assertTrue(ok)
        self.assertIn("id", secured.lower())
        self.assertIn("nombre", secured.lower())
        self.assertNotIn("sueldo", secured.lower())


class TestStarExpansionStaysInScope(unittest.TestCase):
    """`*` solo puede expandir las columnas de SU propio FROM/JOIN."""

    def test_star_over_derived_table_uses_only_derived_columns(self):
        ok, secured, _ = ASTValidator.validate_and_secure_sql(
            "SELECT * FROM (SELECT id FROM empleados) d",
            allowed_tables=EMPLOYEES,
            blocked_columns=BLOCKED,
            table_columns=COLUMNS,
        )
        self.assertTrue(ok)
        self.assertIn("id", secured.lower())
        self.assertNotIn("nombre", secured.lower())

    def test_qualified_star_over_derived_table_is_allowed(self):
        ok, secured, _ = ASTValidator.validate_and_secure_sql(
            "SELECT d.* FROM (SELECT id FROM empleados) d",
            allowed_tables=EMPLOYEES,
            blocked_columns=BLOCKED,
            table_columns=COLUMNS,
        )
        self.assertTrue(ok)
        self.assertIn("d.id", secured.lower())
        self.assertNotIn("nombre", secured.lower())

    def test_star_over_cte_does_not_take_columns_from_its_body(self):
        cases = (
            "WITH x AS (SELECT id FROM empleados) SELECT * FROM x",
            "WITH x AS (SELECT id FROM empleados) SELECT x.* FROM x",
            "WITH a AS (SELECT id FROM empleados), "
            "b AS (SELECT id FROM a) SELECT b.* FROM b",
        )
        for sql in cases:
            with self.subTest(sql=sql):
                ok, secured, _ = ASTValidator.validate_and_secure_sql(
                    sql,
                    allowed_tables=EMPLOYEES,
                    blocked_columns=BLOCKED,
                    table_columns=COLUMNS,
                )
                self.assertTrue(ok)
                self.assertNotIn("nombre", secured.lower())
                self.assertNotIn("empleados.nombre", secured.lower())

    def test_star_over_join_does_not_duplicate_inner_physical_columns(self):
        ok, secured, _ = ASTValidator.validate_and_secure_sql(
            "SELECT * FROM (SELECT id FROM empleados) d "
            "JOIN empleados e ON d.id = e.id",
            allowed_tables=EMPLOYEES,
            blocked_columns=BLOCKED,
            table_columns=COLUMNS,
        )
        self.assertTrue(ok)
        self.assertIn("d.id", secured.lower())
        self.assertIn("e.id", secured.lower())
        self.assertIn("e.nombre", secured.lower())
        self.assertNotIn("empleados.id", secured.lower())
        self.assertNotIn("sueldo", secured.lower())

    def test_inner_cte_with_same_name_does_not_expand_outer_physical_table(self):
        """La CTE `empleados` de la subconsulta no es visible en el JOIN externo.

        Un mapa de CTEs por nombre expandía el `*` de la tabla física externa
        con las columnas de la CTE interna (aquí solo `nombre`), perdiendo
        `id` y cualquier otra columna real.
        """
        sql = (
            "SELECT * FROM ("
            "WITH empleados AS (SELECT id AS nombre FROM dim_clientes) "
            "SELECT * FROM empleados"
            ") sub JOIN empleados e ON sub.nombre = e.id"
        )
        ok, secured, _ = ASTValidator.validate_and_secure_sql(
            sql,
            allowed_tables={"empleados", "dim_clientes"},
            blocked_columns={"sueldo"},
            table_columns={
                "empleados": ["id", "nombre", "sueldo"],
                "dim_clientes": ["id"],
            },
        )
        self.assertTrue(ok)
        self.assertIn("sub.nombre", secured.lower())
        self.assertIn("e.id", secured.lower())
        self.assertIn("e.nombre", secured.lower())
        self.assertNotIn("sueldo", secured.lower())


class TestMutatingSelectFormsAreRejected(unittest.TestCase):

    def test_mutating_select_forms_are_rejected(self):
        with self.assertRaises(ASTValidationError) as ctx:
            ASTValidator.validate_and_secure_sql(
                "SELECT id INTO nueva FROM empleados", allowed_tables=EMPLOYEES
            )
        self.assertIn("prohibida", str(ctx.exception).lower())

        for clause in ("FOR UPDATE", "FOR SHARE"):
            with self.subTest(clause=clause):
                with self.assertRaises(ASTValidationError):
                    ASTValidator.validate_and_secure_sql(
                        f"SELECT id FROM empleados {clause}",
                        allowed_tables=EMPLOYEES,
                    )


class TestSchemaAllowlistByDialect(unittest.TestCase):
    """El esquema por defecto no es `public` en todos los dialectos."""

    def test_tsql_allows_dbo(self):
        ok, secured, _ = ASTValidator.validate_and_secure_sql(
            "SELECT e.id FROM dbo.empleados e",
            dialect="tsql",
            allowed_tables=EMPLOYEES,
            blocked_columns=BLOCKED,
        )
        self.assertTrue(ok)
        self.assertIn("dbo.empleados", secured.lower())

    def test_postgres_still_rejects_dbo(self):
        with self.assertRaises(ASTValidationError):
            ASTValidator.validate_and_secure_sql(
                "SELECT e.id FROM dbo.empleados e",
                dialect="postgres",
                allowed_tables=EMPLOYEES,
                blocked_columns=BLOCKED,
            )

    def test_mysql_rejects_database_qualifier(self):
        with self.assertRaises(ASTValidationError):
            ASTValidator.validate_and_secure_sql(
                "SELECT e.id FROM midb.empleados e",
                dialect="mysql",
                allowed_tables=EMPLOYEES,
                blocked_columns=BLOCKED,
            )

    def test_mysql_without_schema_still_passes(self):
        ok, _, _ = ASTValidator.validate_and_secure_sql(
            "SELECT e.id FROM empleados e",
            dialect="mysql",
            allowed_tables=EMPLOYEES,
            blocked_columns=BLOCKED,
        )
        self.assertTrue(ok)


class TestLegitimateBusinessQueriesStillPass(unittest.TestCase):
    """El endurecimiento no puede romper el SQL que el producto genera."""

    def test_aggregate_join_group_by_and_limit(self):
        sql = (
            "SELECT TO_CHAR(v.fecha, 'YYYY-MM') AS periodo, "
            "SUM(v.monto) AS total, COALESCE(SUM(v.monto), 0) AS total_seguro "
            "FROM fact_ventas v JOIN dim_clientes c ON v.cliente_id = c.id "
            "WHERE v.fecha >= NOW() - INTERVAL '30 days' "
            "GROUP BY TO_CHAR(v.fecha, 'YYYY-MM') "
            "ORDER BY total DESC LIMIT 10"
        )
        ok, secured, meta = ASTValidator.validate_and_secure_sql(
            sql,
            allowed_tables={"fact_ventas", "dim_clientes"},
            blocked_columns={"sueldo"},
        )
        self.assertTrue(ok)
        self.assertIn("LIMIT 10", secured)
        self.assertIn("fact_ventas", meta["tables_used"])
        self.assertIn("dim_clientes", meta["tables_used"])

    def test_string_and_date_functions_are_allowed(self):
        sql = (
            "SELECT AGE(fecha_ingreso) AS antiguedad, "
            "SPLIT_PART(nombre, ' ', 1) AS nombre_corto, "
            "STRING_AGG(DISTINCT nombre, ',') AS nombres, "
            "LENGTH(nombre) AS largo "
            "FROM empleados GROUP BY fecha_ingreso, nombre"
        )
        ok, _, _ = ASTValidator.validate_and_secure_sql(
            sql, allowed_tables=EMPLOYEES, blocked_columns=BLOCKED
        )
        self.assertTrue(ok)

    def test_tsql_bracket_identifiers_are_not_subscripts(self):
        # El rechazo es para `Bracket` (subindice), no para los identificadores
        # entre corchetes de SQL Server: `[nombre]` sigue siendo una columna.
        ok, secured, _ = ASTValidator.validate_and_secure_sql(
            "SELECT [nombre] FROM empleados",
            dialect="tsql",
            allowed_tables=EMPLOYEES,
            blocked_columns=BLOCKED,
        )
        self.assertTrue(ok)
        self.assertIn("nombre", secured.lower())

    def test_array_constructor_is_not_a_subscript(self):
        # `ARRAY[...]` se parsea como `exp.Array`, no como `Bracket`.
        ok, _, _ = ASTValidator.validate_and_secure_sql(
            "SELECT ARRAY[1, 2] AS valores FROM empleados",
            allowed_tables=EMPLOYEES,
            blocked_columns=BLOCKED,
        )
        self.assertTrue(ok)

    def test_make_date_and_interval_functions_are_allowed(self):
        sql = (
            "SELECT MAKE_DATE(2026, 1, 1) AS inicio, "
            "MAKE_INTERVAL(0, 1, 0) AS un_mes "
            "FROM empleados"
        )
        ok, _, _ = ASTValidator.validate_and_secure_sql(
            sql, allowed_tables=EMPLOYEES, blocked_columns=BLOCKED
        )
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()

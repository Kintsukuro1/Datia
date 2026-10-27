import unittest
from unittest.mock import MagicMock
from fastapi import HTTPException
from app.modules.chat_engine.engine import QueryEngine
from app.modules.chat_engine.dynamic_schema import DynamicSchemaPruningService

class TestRBACGovernance(unittest.TestCase):

    def test_admin_gets_all_catalog_tables(self):
        """Admin role queries SemanticCatalog model and gets all catalog tables.

        El universo del admin es "introspeccion fisica MAS catalogo semantico"
        (`get_authorized_schema_prompt`), asi que este test tiene que apagar la
        introspeccion: un `db` magico no controla el disco, y sin apagarla se
        cuela la demo SQLite del proyecto y el admin 've' sus nueve tablas.
        """
        from unittest.mock import patch

        mock_entry1 = MagicMock(table_name="dim_clientes", column_name="id_cliente", description="ID")
        mock_entry2 = MagicMock(table_name="fact_ventas", column_name="id_venta", description="ID")
        mock_db = MagicMock()
        mock_db.query().filter().all.return_value = [mock_entry1, mock_entry2]

        with patch.object(DynamicSchemaPruningService, "get_physical_db_tables", return_value=set()):
            res = QueryEngine.get_allowed_tables_for_role("Administrador", is_admin=True, db=mock_db)
        self.assertEqual(res, {"dim_clientes", "fact_ventas"})

    def test_role_permissions_queried_from_permission_model(self):
        """Non-admin user permissions are dynamically resolved from RoleTablePermission model."""
        mock_perm1 = MagicMock(table_name="fact_ventas", is_allowed=True)
        mock_perm2 = MagicMock(table_name="dim_productos", is_allowed=True)
        mock_db = MagicMock()

        # Mock RoleTablePermission query result
        mock_db.query().filter().all.return_value = [mock_perm1, mock_perm2]

        res = QueryEngine.get_allowed_tables_for_role(
            user_role="Economista",
            is_admin=False,
            db=mock_db,
            role_id=2,
            connection_id=1
        )
        self.assertEqual(res, {"fact_ventas", "dim_productos"})

    def test_role_id_none_unassigned_fails_closed(self):
        """Non-admin user with unassigned role_id=None and non-existent role name returns empty set."""
        from unittest.mock import patch
        from app.modules.catalog.services.schema_inspector import SchemaInspector

        mock_db = MagicMock()
        mock_db.query().filter().first.return_value = None  # No matching Role found
        mock_db.query().filter().all.return_value = []

        # La conexion del pedido es una precondicion, no el sujeto del test (y
        # ya tiene su propia cobertura en `test_catalog_service`). Sin esto, con
        # una base mockeada sin conexiones, el 404 de un id explicito cortaria
        # antes de medir el RBAC.
        with patch.object(SchemaInspector, "resolve_connection", return_value=MagicMock(id=1)), \
             patch.object(DynamicSchemaPruningService, "get_physical_db_tables", return_value=set()):
            res = QueryEngine.get_allowed_tables_for_role(
                user_role="UsuarioSinRol",
                is_admin=False,
                db=mock_db,
                role_id=None,
                connection_id=1
            )
        self.assertEqual(res, set(), "Unassigned role_id=None must return empty set (Fail-Closed)")

    def test_db_revoked_permissions_returns_empty_set(self):
        """When a role has 0 allowed tables configured in RoleTablePermission, returns empty set."""
        mock_db = MagicMock()
        mock_db.query().filter().all.return_value = []

        res = QueryEngine.get_allowed_tables_for_role(
            user_role="Economista",
            is_admin=False,
            db=mock_db,
            role_id=10,
            connection_id=1
        )
        self.assertEqual(res, set())

    def test_db_exception_fails_closed(self):
        """Con la matriz RBAC caída no se inventan permisos: 503.

        Devolver `set()` funcionaba para `allowed_tables` (deniega), pero para
        `blocked_columns`/`masked_columns` el mismo vacío significaba "no hay
        nada bloqueado", o sea fail-open. El guard ahora corta con 503 en los
        tres campos.
        """
        mock_db = MagicMock()
        mock_db.query.side_effect = Exception("Database connection lost")

        with self.assertRaises(HTTPException) as ctx:
            QueryEngine.get_allowed_tables_for_role(
                user_role="Economista",
                is_admin=False,
                db=mock_db,
                role_id=10,
                connection_id=1
            )
        self.assertEqual(ctx.exception.status_code, 503)

    def test_column_permissions_queried_from_permission_model(self):
        """Blocked columns are dynamically queried from RoleColumnPermission model."""
        mock_col_perm = MagicMock(
            table_name="fact_ventas",
            column_name="cuenta_bancaria_iban",
            permission_type=MagicMock(value="BLOCKED")
        )
        # Match enum comparison inside dynamic_schema
        from app.modules.admin_catalog.models import ColumnPermissionType
        mock_col_perm.permission_type = ColumnPermissionType.BLOCKED

        mock_db = MagicMock()
        mock_db.query().filter().all.return_value = [mock_col_perm]

        res = QueryEngine.get_blocked_columns_for_role(
            user_role="Economista",
            is_admin=False,
            db=mock_db,
            role_id=2,
            connection_id=1
        )
        self.assertIn("cuenta_bancaria_iban", res)

    def test_ti_role_asking_financial_questions_blocked_by_governance(self):
        """TI role asking for sales/balances without business tables is denied by cross-domain guardrail."""
        denial = QueryEngine.check_domain_governance(
            question="¿Cuáles fueron los saldos y ventas totales del mes?",
            user_role="TI",
            allowed_tables={"dim_servidores", "fact_incidentes_ti"}
        )
        self.assertIsNotNone(denial)
        self.assertIn("Gobernanza RBAC: Acceso denegado", denial)
        self.assertIn("información financiera", denial)

    def test_economista_role_asking_infrastructure_questions_blocked_by_governance(self):
        """Economista role asking for servers/IT incidents without tech tables is denied by cross-domain guardrail."""
        denial = QueryEngine.check_domain_governance(
            question="¿Cuántos incidentes ti ocurrieron en los servidores?",
            user_role="Economista",
            allowed_tables={"fact_ventas", "dim_clientes"}
        )
        self.assertIsNotNone(denial)
        self.assertIn("Gobernanza RBAC: Acceso denegado", denial)
        self.assertIn("infraestructura TI", denial)

    def test_admin_no_se_deduce_del_nombre_del_rol(self):
        """Un nombre con "admin" o "ti" adentro no otorga ni quita nada.

        Antes `check_domain_governance` clasificaba por substring: "Administrativo
        de TI" entraba por el bypass de admin ("admin") y "Coordinador de Gestion"
        se trataba como infraestructura por el "ti" de "Gestion". El bypass real
        ahora es explícito (`is_admin` o el rol exacto de `ADMIN_ROLES`).
        """
        denial = QueryEngine.check_domain_governance(
            question="¿Cuáles son las ventas totales del mes?",
            user_role="Administrativo de TI",
            allowed_tables={"fact_incidentes_ti"},
        )
        self.assertIsNotNone(denial)
        self.assertIn("información financiera", denial)

        self.assertIsNone(QueryEngine.check_domain_governance(
            question="¿Cuáles son las ventas totales del mes?",
            user_role="Administrativo de TI",
            allowed_tables=set(),
            is_admin=True,
        ))

    def test_la_matriz_decide_y_no_el_nombre_del_rol(self):
        """Misma pregunta y misma matriz: mismo veredicto, se llame como se llame.

        Y al revés: con matrices distintas, el veredicto cambia aunque el nombre
        sea el mismo. Es el contrato que reemplaza a la clasificación por
        substring.
        """
        pregunta = "¿Cuáles son las ventas totales del mes?"
        tablas_finanzas = {"fact_ventas", "dim_clientes"}
        tablas_ti = {"dim_servidores", "fact_incidentes_ti"}

        for rol in (
            "Finance Analyst",
            "Ingeniero de Infraestructura & TI",
            "Coordinador de Gestión",
            "Analista de Datos & BI",
        ):
            with self.subTest(rol=rol, matriz="finanzas"):
                self.assertIsNone(QueryEngine.check_domain_governance(
                    pregunta, rol, tablas_finanzas))
            with self.subTest(rol=rol, matriz="ti"):
                denial = QueryEngine.check_domain_governance(pregunta, rol, tablas_ti)
                self.assertIsNotNone(denial)
                self.assertIn("información financiera", denial)

    def test_la_matriz_tambien_decide_en_recursos_humanos(self):
        """Con `dim_empleados` la pregunta de sueldos pasa la capa 1; sin ella no.

        El valor del sueldo sigue protegido por la capa de columnas
        (`sueldo_mensual` BLOCKED/MASKED) y por el AST: esta capa solo decide si
        la pregunta cae dentro de las tablas del rol.
        """
        pregunta = "¿Cuáles son los sueldos del personal?"
        self.assertIsNone(QueryEngine.check_domain_governance(
            pregunta, "Cualquiera", {"dim_empleados"}))
        denial = QueryEngine.check_domain_governance(
            pregunta, "Cualquiera", {"fact_ventas"})
        self.assertIsNotNone(denial)
        self.assertIn("salarios, remuneraciones, nóminas", denial)

    def test_el_catalogo_puede_preguntar_por_precios(self):
        """`precio` no es una keyword financiera: el catálogo es consultable.

        La matriz declara `dim_categorias`/`dim_productos` como lectura de
        cualquier perfil. Negarle "¿cuánto cuesta el producto X?" por "precio"
        contradecía esa misma matriz.
        """
        self.assertIsNone(QueryEngine.check_domain_governance(
            "¿Cuál es el precio de los productos?",
            "Usuario Consultor",
            {"dim_categorias", "dim_productos"},
        ))

    def test_matriz_sin_tablas_clasificables_no_dispara_el_guard(self):
        """Nombres arbitrarios (`movimientos`, `hoja1`) no son un dominio ajeno.

        Un cliente sube su CSV con el nombre que usa en su empresa. Si NINGUNA
        tabla del rol se puede clasificar, no hay evidencia de cruce de dominio:
        negarle "ingresos" al duenio de la unica tabla seria negarle su propio
        dato. El SQL sigue acotado por la matriz y por el AST.

        Es el falso positivo que destapo `test_ml_service_rbac`: un rol con
        `movimientos` recibia 403 al proyectar "los ingresos mensuales".
        """
        self.assertIsNone(QueryEngine.check_domain_governance(
            "proyecta los ingresos mensuales",
            "Rol con dataset propio",
            {"movimientos"},
        ))

        # Limite conocido de la heuristica: si la matriz ya declara un dominio,
        # se asume que esta estructurada por areas y la pregunta del area
        # ausente se deniega. Un nombre generico junto a tablas clasificadas no
        # se adivina.
        denial = QueryEngine.check_domain_governance(
            "proyecta los ingresos mensuales",
            "Rol con dataset propio",
            {"movimientos", "dim_servidores"},
        )
        self.assertIsNotNone(denial)
        self.assertIn("información financiera", denial)

    def test_admin_role_can_query_any_domain(self):
        """Domain governance does not block allowed queries when roles match their domain."""
        # TI querying IT infrastructure
        ti_denial = QueryEngine.check_domain_governance(
            question="¿Cuántos incidentes ti ocurrieron en los servidores?",
            user_role="TI",
            allowed_tables={"dim_servidores", "fact_incidentes_ti"}
        )
        self.assertIsNone(ti_denial)

        # Economista querying sales
        eco_denial = QueryEngine.check_domain_governance(
            question="¿Cuáles son las ventas totales por cliente?",
            user_role="Economista",
            allowed_tables={"fact_ventas", "dim_clientes"}
        )
        self.assertIsNone(eco_denial)

    def test_ast_validation_error_in_execute_with_self_healing_propagates(self):
        """execute_with_self_healing strictly raises ASTValidationError on unauthorized table query (no silent pass)."""
        import asyncio
        from app.modules.chat_engine.sql_executor import SQLExecutor
        from app.modules.chat_engine.ast_validator import ASTValidationError

        unauthorized_sql = "SELECT * FROM fact_ventas WHERE id = 1;"
        ti_allowed_tables = {"dim_servidores", "fact_incidentes_ti"}

        with self.assertRaises(ASTValidationError):
            asyncio.run(SQLExecutor.execute_with_self_healing(
                target_db_path="./datia_demo.db",
                question="ventas",
                initial_sql=unauthorized_sql,
                allowed_tables=ti_allowed_tables,
                blocked_columns=set(),
                table_columns_map={"dim_servidores": ["id", "nombre"]}
            ))

    def test_execute_query_blocks_ti_for_financial_question(self):
        """QueryEngine.execute_query cleanly rejects TI asking for sales/financial data with RECHAZADO_RBAC."""
        import asyncio
        mock_db = MagicMock()
        # Mock TI permissions having only server tables
        mock_perm = MagicMock(table_name="dim_servidores", is_allowed=True)
        mock_db.query().filter().all.return_value = [mock_perm]

        resp = asyncio.run(QueryEngine.execute_query(
            question="¿Cuáles son los saldos totales de ventas del año?",
            user_role="TI",
            is_admin=False,
            db=mock_db,
            role_id=11,
            connection_id=1
        ))

        self.assertEqual(resp.traceability.validation_status, "RECHAZADO_RBAC")
        self.assertIn("Gobernanza RBAC: Acceso denegado", resp.summary_text)
        self.assertIn("información financiera", resp.summary_text)

    def test_execute_query_blocks_economista_for_infrastructure_question(self):
        """QueryEngine.execute_query cleanly rejects Economista asking for server/IT data with RECHAZADO_RBAC."""
        import asyncio
        mock_db = MagicMock()
        # Mock Economista permissions having only sales tables
        mock_perm = MagicMock(table_name="fact_ventas", is_allowed=True)
        mock_db.query().filter().all.return_value = [mock_perm]

        resp = asyncio.run(QueryEngine.execute_query(
            question="¿Cuántos incidentes ti ocurrieron en los servidores?",
            user_role="Economista",
            is_admin=False,
            db=mock_db,
            role_id=10,
            connection_id=1
        ))

        self.assertEqual(resp.traceability.validation_status, "RECHAZADO_RBAC")
        self.assertIn("Gobernanza RBAC: Acceso denegado", resp.summary_text)
        self.assertIn("infraestructura TI", resp.summary_text)

    def test_financial_analyst_asking_it_infrastructure_and_modules_blocked(self):
        """Financial Analyst asking about IT modules, infrastructure, tickets or tech is strictly blocked."""
        it_questions = [
            "Hazme un analisis tecnologico",
            "Hazme un análisis tecnológico",
            "Hazme un analisis de tecnologia",
            "¿Cuál es el estado de la infraestructura de TI?",
            "¿Cuáles son los incidentes técnicos de soporte?",
            "¿Cómo está el módulo de tecnología?",
            "¿Qué servidores están activos?",
            "¿Cuál es el consumo de CPU y memoria de los servidores?",
            "Dame un reporte del área de TI",
            "¿Cuáles son los tickets de soporte técnico?"
        ]
        for q in it_questions:
            denial = QueryEngine.check_domain_governance(
                question=q,
                user_role="Analista Financiero & Comercial",
                allowed_tables={"fact_ventas", "dim_clientes"}
            )
            self.assertIsNotNone(denial, f"Question '{q}' should be blocked for Analista Financiero")
            self.assertIn("Gobernanza RBAC: Acceso denegado", denial)
            self.assertIn("infraestructura TI", denial)

    def test_greeting_with_cross_domain_it_question_is_blocked(self):
        """Conversational greeting containing out-of-domain IT questions is rejected by RBAC before greeting branch."""
        import asyncio
        mock_db = MagicMock()
        mock_perm = MagicMock(table_name="fact_ventas", is_allowed=True)
        mock_db.query().filter().all.return_value = [mock_perm]

        resp = asyncio.run(QueryEngine.execute_query(
            question="Hola, ¿cuántos servidores hay en el módulo de TI?",
            user_role="Analista Financiero & Comercial",
            is_admin=False,
            db=mock_db,
            role_id=3,
            connection_id=1
        ))
        self.assertEqual(resp.traceability.validation_status, "RECHAZADO_RBAC")
        self.assertIn("Gobernanza RBAC: Acceso denegado", resp.summary_text)

    def test_ti_asking_billing_money_and_financial_module_blocked(self):
        """TI role asking for money, billing, or financial modules is strictly blocked."""
        fin_questions = [
            "¿Cuánto dinero se recaudó este mes?",
            "¿Cuál es la facturación del período?",
            "¿Cómo va el módulo financiero?",
            "¿Cuáles son los márgenes de ganancia y EBITDA?"
        ]
        for q in fin_questions:
            denial = QueryEngine.check_domain_governance(
                question=q,
                user_role="Ingeniero de Infraestructura & TI",
                allowed_tables={"dim_servidores", "fact_incidentes_ti"}
            )
            self.assertIsNotNone(denial, f"Question '{q}' should be blocked for TI")
            self.assertIn("Gobernanza RBAC: Acceso denegado", denial)
            self.assertIn("información financiera", denial)

    def test_non_hr_asking_salaries_and_payroll_blocked(self):
        """Non-HR roles asking for salaries or employee compensation are strictly blocked."""
        salary_questions = [
            "¿Cuáles son los sueldos del personal?",
            "¿Cuánto gana el CTO o directores?",
            "Dame la nómina de remuneraciones",
            "¿Cuáles son los salarios por departamento?"
        ]
        for role in ["Analista Financiero & Comercial", "Ingeniero de Infraestructura & TI", "Economista", "TI"]:
            for q in salary_questions:
                denial = QueryEngine.check_domain_governance(
                    question=q,
                    user_role=role,
                    allowed_tables={"fact_ventas"}
                )
                self.assertIsNotNone(denial, f"Question '{q}' should be blocked for {role}")
                self.assertIn("Gobernanza RBAC: Acceso denegado", denial)
                self.assertIn("salarios, remuneraciones, nóminas", denial)

    def test_admin_never_blocked_by_domain_governance(self):
        """Admin role can query across all domains (financial, IT, HR) without RBAC block."""
        admin_questions = [
            "¿Cuáles son los sueldos del personal?",
            "¿Cuál es la facturación del período?",
            "Estado de servidores y CPU",
            "Compara los ingresos contra los costos por producto y dime cuáles tienen menor margen"
        ]
        for q in admin_questions:
            denial = QueryEngine.check_domain_governance(
                question=q,
                user_role="Administrador",
                allowed_tables={"fact_ventas", "dim_servidores", "dim_empleados"},
                is_admin=True,
            )
            self.assertIsNone(denial, f"Admin should NEVER be blocked by RBAC on question '{q}'")

if __name__ == "__main__":
    unittest.main()

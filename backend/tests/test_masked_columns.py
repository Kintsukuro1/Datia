"""Las columnas MASKED se consultan pero su valor no sale en claro.

El enum `ColumnPermissionType` define ALLOWED / BLOCKED / MASKED, e `init_db` siembra
`dim_clientes.rut_dni_cliente` como MASKED para todos los roles. Antes de este fix
MASKED no hacia nada: no entraba a `blocked_columns` (asi que el validador lo dejaba
pasar) y ningun modulo transformaba el valor, asi que un Economista leia el RUT/DNI
en claro. Habia enum, seed, `[ENMASCARADO]` en el prompt y un docstring que promete
"column masking"; nada de eso tocaba un valor.

Regla acordada: se conservan los ultimos 4 DIGITOS.
"""

import json
import unittest

from main import app  # noqa: F401  registra los mappers de SQLAlchemy

from app.core.database import SessionLocal
from app.core.constants import (
    ROLE_ANALISTA_FINANCIERO, ROLE_DIRECTOR_EJECUTIVO,
    ROLE_GERENTE_TALENTO, ROLE_ANALISTA_BI, ROLE_INGENIERO_TI,
    ROLE_OFICIAL_SEGURIDAD, ROLE_USUARIO,
)
from app.core.security import mask_query_rows, mask_rows, mask_value
from app.modules.chat_engine.governance_guard import GovernanceGuard


class TestMaskValue(unittest.TestCase):
    def test_mask_value_contract(self):
        # Regla acordada: se conservan los ultimos 4 DIGITOS, no caracteres.
        cases = (
            ("rut", "12.345.678-9", "*******678-9"),
            ("rut_distinto", "98.765.432-4", "*******432-4"),
            ("sin_digitos", "texto sin numeros", "*" * len("texto sin numeros")),
            ("corto", "123", "***"),
            ("vacio", "", ""),
            ("null", None, None),
        )
        for name, original, expected in cases:
            with self.subTest(case=name):
                self.assertEqual(mask_value(original), expected)

        # Los ultimos 4 CARACTERES serian "78-9" y perderian el 6. Ademas el
        # valor original no debe ser recuperable y el largo se conserva.
        masked = mask_value("12.345.678-9")
        self.assertTrue(masked.endswith("678-9"), masked)
        self.assertNotIn("12.345", masked)
        self.assertEqual(len(masked), len("12.345.678-9"))


class TestMaskRows(unittest.TestCase):
    def test_masks_only_the_named_columns_case_insensitively(self):
        rows = [{
            "nombre": "Ana Perez",
            "rut_dni_cliente": "12.345.678-9",
            "email": "ana@empresa.com",
        }]
        touched = mask_rows(rows, {"rut_dni_cliente"})

        self.assertEqual(touched, 1)
        self.assertEqual(rows[0]["rut_dni_cliente"], "*******678-9")
        self.assertEqual(rows[0]["nombre"], "Ana Perez")
        self.assertEqual(rows[0]["email"], "ana@empresa.com")

        # El motor puede devolver la clave con el casing usado en la consulta.
        uppercase = [{"RUT_DNI_CLIENTE": "12.345.678-9"}]
        mask_rows(uppercase, {"rut_dni_cliente"})
        self.assertEqual(uppercase[0]["RUT_DNI_CLIENTE"], "*******678-9")

    def test_empty_inputs_are_noop(self):
        rows = [{"rut_dni_cliente": "12.345.678-9"}]
        self.assertEqual(mask_rows(rows, set()), 0)
        self.assertEqual(mask_rows(rows, None), 0)
        self.assertEqual(mask_rows([], {"rut_dni_cliente"}), 0)
        self.assertEqual(rows[0]["rut_dni_cliente"], "12.345.678-9")


class TestMaskQueryRows(unittest.TestCase):
    """`mask_rows` mira la clave; el alias del SELECT puede cambiar la clave."""

    def test_masks_alias_of_masked_column_in_cte_and_aggregate(self):
        cases = (
            (
                "alias_directo",
                [{"identificador": "12.345.678-9", "nombre": "Ana Perez"}],
                "SELECT rut_dni_cliente AS identificador, nombre FROM dim_clientes",
                "identificador",
                "nombre",
            ),
            (
                "alias_en_cte",
                [{"r": "12.345.678-9"}],
                "WITH x AS (SELECT rut_dni_cliente AS r FROM dim_clientes) SELECT r FROM x",
                "r",
                None,
            ),
            (
                # El alias del CTE es una fuente MASKED: si la proyeccion
                # externa lo agrega, el alias externo tambien se enmascara.
                "alias_agregado",
                [{"max_rut": "12.345.678-9"}],
                (
                    "WITH x AS (SELECT rut_dni_cliente AS r FROM dim_clientes) "
                    "SELECT MAX(r) AS max_rut FROM x"
                ),
                "max_rut",
                None,
            ),
        )
        for name, rows, sql, key, other_key in cases:
            with self.subTest(case=name):
                mask_query_rows(rows, {"rut_dni_cliente"}, sql)
                self.assertEqual(rows[0][key], "*******678-9")
                if other_key:
                    self.assertEqual(rows[0][other_key], "Ana Perez")

    def test_unaliased_expression_and_json_subscript_mask_the_whole_row(self):
        # Sin alias la clave la decide el driver; con `perfil['rut']` es un
        # subscript JSONB, no JSONExtract. En ambos casos no hay una clave
        # estable que buscar y se enmascara la fila completa.
        cases = (
            (
                "sin_alias",
                "SELECT rut_dni_cliente || 'x', nombre FROM dim_clientes",
                "?column?",
            ),
            (
                "subscript_json",
                "SELECT perfil['rut_dni_cliente'] AS x, nombre FROM dim_clientes",
                "x",
            ),
        )
        for name, sql, key in cases:
            with self.subTest(case=name):
                rows = [{key: "12.345.678-9", "nombre": "Ana Perez"}]
                mask_query_rows(rows, {"rut_dni_cliente"}, sql)
                self.assertEqual(rows[0][key], "*******678-9")
                self.assertEqual(rows[0]["nombre"], "*********")

    def test_query_without_masked_usage_or_sql_leaves_rows_untouched(self):
        rows = [{"total": 3}]
        mask_query_rows(rows, {"rut_dni_cliente"}, "SELECT COUNT(*) AS total FROM dim_clientes")
        self.assertEqual(rows[0], {"total": 3})

        # Sin SQL, el fallback mira la clave directa de la fila.
        direct = [{"rut_dni_cliente": "12.345.678-9"}]
        mask_query_rows(direct, {"rut_dni_cliente"})
        self.assertEqual(direct[0]["rut_dni_cliente"], "*******678-9")


class TestMaskQueryRowsJson(unittest.TestCase):
    """Defensa en profundidad: una columna JSON tambien pasa por el enmascarado.

    `mask_rows` compara la CLAVE de la fila con el nombre de la columna MASKED.
    Una columna JSON puede llevar el dato sensible adentro y la clave de la fila
    no lo delata, asi que el valor saldria en claro.
    """

    def test_json_structures_with_masked_key(self):
        with self.subTest(case="objeto"):
            rows = [{"registro": '{"sueldo": 123456, "nombre": "Ana"}'}]
            touched = mask_query_rows(rows, {"sueldo"})
            doc = json.loads(rows[0]["registro"])
            self.assertEqual(doc["sueldo"], mask_value(123456))
            self.assertEqual(doc["nombre"], "Ana")
            self.assertEqual(touched, 1)

        with self.subTest(case="anidado"):
            rows = [{"perfil": {"datos": {"sueldo": 123456, "area": "TI"}}}]
            touched = mask_query_rows(rows, {"sueldo"})
            self.assertEqual(rows[0]["perfil"]["datos"]["sueldo"], mask_value(123456))
            self.assertEqual(rows[0]["perfil"]["datos"]["area"], "TI")
            self.assertEqual(touched, 1)

        with self.subTest(case="lista"):
            rows = [{
                "empleados": [
                    {"sueldo": 111111},
                    {"sueldo": 222222, "nombre": "Luis"},
                ]
            }]
            touched = mask_query_rows(rows, {"sueldo"})
            self.assertEqual(rows[0]["empleados"][0]["sueldo"], mask_value(111111))
            self.assertEqual(rows[0]["empleados"][1]["sueldo"], mask_value(222222))
            self.assertEqual(rows[0]["empleados"][1]["nombre"], "Luis")
            self.assertEqual(touched, 2)

        with self.subTest(case="array_string"):
            rows = [{"registro": '[{"sueldo": 123456}, {"sueldo": 654321}]'}]
            mask_query_rows(rows, {"sueldo"})
            doc = json.loads(rows[0]["registro"])
            self.assertEqual(doc[0]["sueldo"], mask_value(123456))
            self.assertEqual(doc[1]["sueldo"], mask_value(654321))

    def test_key_comparison_is_case_insensitive(self):
        rows = [{"perfil": {"SUELDO": 123456}}]
        mask_query_rows(rows, {"sueldo"})
        self.assertEqual(rows[0]["perfil"]["SUELDO"], mask_value(123456))

    def test_json_without_masked_keys_is_untouched(self):
        cases = (
            ("texto_normal", "texto normal, sin json"),
            ("json_invalido", '{"sueldo": 123'),
            ("json_sin_clave", '{"a":1}'),
        )
        for name, original in cases:
            with self.subTest(case=name):
                rows = [{"registro": original}]
                touched = mask_query_rows(rows, {"sueldo"})
                self.assertEqual(rows[0]["registro"], original)
                self.assertEqual(touched, 0)


class TestMaskedColumnsAreGovernedSeparately(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()

    def tearDown(self):
        self.db.close()

    def test_masked_column_is_not_blocked(self):
        """MASKED debe seguir consultable: si se bloqueara, el LLM no puede join-ear."""
        blocked = GovernanceGuard.get_blocked_columns_for_role(
            ROLE_ANALISTA_FINANCIERO, False, db=self.db, role_id=None, connection_id=1
        )
        masked = GovernanceGuard.get_masked_columns_for_role(
            ROLE_ANALISTA_FINANCIERO, False, db=self.db, role_id=None, connection_id=1
        )

        self.assertIn("rut_dni_cliente", masked)
        self.assertNotIn(
            "rut_dni_cliente", blocked,
            "MASKED no debe agregarse a blocked_columns o la consulta se rechaza entera",
        )

    def test_blocked_columns_are_unaffected(self):
        """El fix de MASKED no debe relajar el bloqueo de las columnas criticas."""
        blocked = GovernanceGuard.get_blocked_columns_for_role(
            ROLE_ANALISTA_FINANCIERO, False, db=self.db, role_id=None, connection_id=1
        )
        for critical in ("tarjeta_credito_token", "sueldo_mensual", "salario"):
            self.assertIn(critical, blocked, f"{critical} dejo de estar bloqueada")

    def test_admin_sees_masked_columns_in_clear(self):
        masked = GovernanceGuard.get_masked_columns_for_role(
            "admin", True, db=self.db, role_id=None, connection_id=1
        )
        self.assertEqual(masked, set())

    def test_el_director_no_ve_los_secretos(self):
        """El C-Level mira rentabilidad global; no necesita el instrumento de pago de
        un cliente ni la cuenta bancaria de un empleado.

        El bug: `init_db` excluia al Director Ejecutivo de la CLS junto al admin
        (`~Role.name.in_([ADMIN, DIRECTOR_EJECUTIVO])`), asi que le llegaba en
        claro `tarjeta_credito_token`, `cuenta_bancaria_iban` y los sueldos. Es
        el unico rol no-admin que quedaba fuera de la matriz de columnas.
        """
        bloqueadas = GovernanceGuard.get_blocked_columns_for_role(
            ROLE_DIRECTOR_EJECUTIVO, False, db=self.db, role_id=None, connection_id=1
        )
        # `tarjeta_credito_token` y `api_key_servicio` viven en tablas que el
        # Director ya no ve (dim_clientes, dim_servidores), asi que la columna no
        # se le puede filtrar aunque la matriz se lo conceda. Lo que se verifica
        # aca es la parte que SI es suya: leer la plantilla sin remuneraciones.
        for secreto in ("sueldo_mensual", "salario", "salario_bruto",
                        "bono_anual", "cuenta_bancaria_iban"):
            self.assertIn(
                secreto, bloqueadas,
                f"El Director Ejecutivo tiene '{secreto}' en claro. Un rol de "
                f"direccion no es el area que responde por ese dato.",
            )

    def test_el_gerente_de_talento_es_el_unico_que_ve_los_sueldos(self):
        """Contrapunto del anterior: bloquear los sueldos a todos dejaria a RRHH sin
        poder hacer su trabajo. El bloqueo tiene que discriminate por rol, no por
        coluna para todos."""
        rrhh = GovernanceGuard.get_blocked_columns_for_role(
            ROLE_GERENTE_TALENTO, False, db=self.db, role_id=None, connection_id=1
        )
        for sueldo in ("sueldo_mensual", "salario_bruto"):
            self.assertNotIn(
                sueldo, rrhh,
                f"El Gerente de Talento necesita ver '{sueldo}': es el area que "
                f"responde por la remuneracion.",
            )

    def test_el_admin_es_el_unico_sin_restricciones_de_columna(self):
        """Ningun otro rol puede tener las columnas criticas en claro a la vez."""
        criticas = ("tarjeta_credito_token", "cuenta_bancaria_iban", "sueldo_mensual")
        sin_bloqueo = []
        for nombre in (ROLE_DIRECTOR_EJECUTIVO, ROLE_ANALISTA_FINANCIERO,
                       ROLE_GERENTE_TALENTO, ROLE_ANALISTA_BI,
                       ROLE_INGENIERO_TI, ROLE_OFICIAL_SEGURIDAD, ROLE_USUARIO):
            bloqueadas = GovernanceGuard.get_blocked_columns_for_role(
                nombre, False, db=self.db, role_id=None, connection_id=1
            )
            if criticas[0] not in bloqueadas:
                sin_bloqueo.append(nombre)
        self.assertEqual(
            sin_bloqueo, [],
            f"Estos roles tienen 'tarjeta_credito_token' en claro: {sin_bloqueo}. "
            f"El admin es el unico con columna sin restringir.",
        )


class TestMaskingReachesTheResponse(unittest.TestCase):
    """End-to-end: la mascara tiene que estar en la respuesta, no solo en la funcion.

    Los tests de arriba pasarian igual si el punto de aplicacion en engine.py
    estuviera mal o si rows se consumiera antes de enmascarar. Este test atraviesa
    el motor completo y comprueba la fila que ve el usuario.
    """

    @classmethod
    def setUpClass(cls):
        from conftest import create_temp_pg_database

        cls.pg_db = create_temp_pg_database("masked")

    @classmethod
    def tearDownClass(cls):
        from conftest import drop_temp_pg_database

        drop_temp_pg_database(cls.pg_db)

    def setUp(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from sqlalchemy import text

        from conftest import pg_source_conn_attrs, pg_source_engine

        from app.modules.chat_engine.engine import QueryEngine

        self.QueryEngine = QueryEngine

        eng = pg_source_engine(self.pg_db)
        try:
            with eng.begin() as c:
                c.execute(text("DROP TABLE IF EXISTS clientes"))
                c.execute(text("CREATE TABLE clientes (nombre TEXT, rut_dni_cliente TEXT)"))
                c.execute(text("INSERT INTO clientes VALUES ('Ana Perez', '12.345.678-9')"))
                c.execute(text("INSERT INTO clientes VALUES ('Luis Soto', '98.765.432-4')"))
        finally:
            eng.dispose()

        mock_conn = MagicMock()
        # Id inexistente + `is_uploaded=True`: el introspector de `SchemaInspector`
        # filtra por los permisos de la conexion subida; con un id real el filtro
        # dependeria del estado de la base de metadatos de tests.
        mock_conn.id = 999999
        mock_conn.is_uploaded = True
        for key, value in pg_source_conn_attrs(self.pg_db).items():
            setattr(mock_conn, key, value)
        mock_conn.null_policy = "open"
        mock_conn.name = "test"

        self.mock_db = MagicMock()
        self.mock_db.query.return_value.filter.return_value.first.return_value = mock_conn
        self.mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = mock_conn
        self.mock_db.query.return_value.order_by.return_value.first.return_value = mock_conn

        # El rol tiene la columna MASKED y NO bloqueada: consultable, valor tapado.
        self.patches = [
            patch.object(QueryEngine, "get_masked_columns_for_role",
                         classmethod(lambda cls, *a, **k: {"rut_dni_cliente"})),
            patch.object(QueryEngine, "get_blocked_columns_for_role",
                         classmethod(lambda cls, *a, **k: set())),
            patch.object(QueryEngine, "get_allowed_tables_for_role",
                         classmethod(lambda cls, *a, **k: {"clientes"})),
            # Lo que este test verifica son las filas enmascaradas, no la
            # narrativa: sin esto cada corrida espera el timeout real del LLM.
            patch("app.modules.chat_engine.llm_service.LLMService.generate_completion",
                  new=AsyncMock(return_value=None)),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_rut_never_reaches_the_response_in_clear(self):
        import asyncio
        response = asyncio.run(
            self.QueryEngine.execute_query(
                question="SELECT nombre, rut_dni_cliente FROM clientes",
                user_role=ROLE_ANALISTA_FINANCIERO,
                is_admin=False,
                db=self.mock_db,
                connection_id=1,
            )
        )

        rows = getattr(response, "data_rows", None)
        self.assertTrue(rows, f"la consulta no devolvio filas: {response}")
        self.assertEqual(len(rows), 2)

        for row in rows:
            self.assertIn("rut_dni_cliente", row)
            self.assertNotIn(
                "12.345.678-9", str(row),
                "el RUT en claro llego a la respuesta del usuario",
            )
            self.assertRegex(
                row["rut_dni_cliente"], r"^\*+\d{3}-\d$",
                f"el RUT no quedo enmascarado con la regla acordada: {row['rut_dni_cliente']}",
            )

        # El resto de la fila debe seguir intacto: enmascarar no rompe la consulta.
        self.assertIn("nombre", rows[0])
        self.assertTrue(rows[0]["nombre"])


if __name__ == "__main__":
    unittest.main()

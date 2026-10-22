"""CLS en tres caminos que no pasaban por el validador ni por la matriz de columnas.

Los tres defectos comparten la misma forma: una columna sin permiso se usa como
si fuera dato publico de negocio.

1. `/system/anomalies` elegia `num_cols[0]` sin mirar BLOCKED/MASKED y ejecutaba
   con `execute_raw_sql`, sin `ASTValidator`. Si la primera numerica era una
   columna enmascarada, el endpoint describia sus valores reales.
2. `forecast_service.audit_data_quality` elegia fecha, metrica, tipo y claves de
   duplicados sobre TODAS las columnas fisicas y ejecutaba SQL crudo. Una columna
   MASKED podia terminar como metrica del reporte de calidad.
3. `dynamic_schema` metia hasta dos muestras reales de una columna MASKED en el
   prompt del LLM antes de agregar `[ENMASCARADO]`. La marca de enmascarado
   llegaba DESPUES del valor que la mascara existe para tapar.
"""

import unittest
from unittest.mock import MagicMock, patch

from app.modules.chat_engine import forecast_service
from app.modules.chat_engine.dynamic_schema import DynamicSchemaPruningService
from app.modules.chat_engine.forecast_service import ForecastUnavailable


# ---------------------------------------------------------------------------
# 1. El prompt del LLM no lleva muestras de columnas MASKED (ni BLOCKED)
# ---------------------------------------------------------------------------

SENTINELA_MASKED = "SENTINELA-9999-RUT"
SENTINELA_BLOCKED = "SENTINELA-8888-SUELDO"


def _permiso_columna(tabla, columna, tipo):
    from app.modules.admin_catalog.models import ColumnPermissionType

    perm = MagicMock()
    perm.table_name = tabla
    perm.column_name = columna
    perm.permission_type = getattr(ColumnPermissionType, tipo)
    return perm


class TestPromptNoExponeMuestrasEnmascaradas(unittest.TestCase):
    def setUp(self):
        DynamicSchemaPruningService.invalidate_schema_cache()

    def tearDown(self):
        DynamicSchemaPruningService.invalidate_schema_cache()

    def _prompt(self):
        tabla = "dim_clientes"
        perm_tabla = MagicMock(table_name=tabla, is_allowed=True)
        col_perms = [
            _permiso_columna(tabla, "rut_dni_cliente", "MASKED"),
            _permiso_columna(tabla, "sueldo_mensual", "BLOCKED"),
        ]
        cols = [
            {
                "name": "rut_dni_cliente",
                "type": "character varying(20)",
                "is_pk": False,
                "samples": [SENTINELA_MASKED],
            },
            {
                "name": "sueldo_mensual",
                "type": "numeric(12,2)",
                "is_pk": False,
                "samples": [SENTINELA_BLOCKED],
            },
            {
                "name": "nombre",
                "type": "character varying(80)",
                "is_pk": False,
                "samples": ["Ana Perez"],
            },
        ]

        db = MagicMock()
        # Orden de los `.all()`: table_perms, col_perms, catalog_entries.
        db.query().filter().all.side_effect = [[perm_tabla], col_perms, []]

        with patch.object(DynamicSchemaPruningService, "get_physical_db_tables",
                          return_value={tabla}), \
             patch.object(DynamicSchemaPruningService, "get_physical_table_columns",
                          return_value=cols):
            return DynamicSchemaPruningService.get_authorized_schema_prompt(
                db=db,
                role_id=2,
                connection_id=77,
                is_admin=False,
                user_role="Economista",
            )["schema_prompt"]

    def test_el_valor_enmascarado_no_aparece_en_el_prompt(self):
        prompt = self._prompt()
        self.assertNotIn(
            SENTINELA_MASKED, prompt,
            "una muestra real de una columna MASKED llego al prompt del LLM",
        )

    def test_la_columna_enmascarada_si_aparece_marcada_y_con_tipo(self):
        """El modelo necesita saber que la columna existe y que esta tapada."""
        prompt = self._prompt()
        self.assertIn("rut_dni_cliente", prompt)
        self.assertIn("ENMASCARADO", prompt)
        self.assertIn("character varying", prompt)

    def test_la_columna_bloqueada_no_aparece_ni_con_su_muestra(self):
        prompt = self._prompt()
        self.assertNotIn("sueldo_mensual", prompt)
        self.assertNotIn(SENTINELA_BLOCKED, prompt)

    def test_las_muestras_de_columnas_permitidas_siguen_apareciendo(self):
        """El corte es por columna, no un apagado global de muestras."""
        self.assertIn("Ana Perez", self._prompt())


# ---------------------------------------------------------------------------
# 2. `/system/anomalies`: la metrica elegida pasa por la matriz de columnas
# ---------------------------------------------------------------------------

class TestAnomaliesScanExcluyeColumnasSensibles(unittest.TestCase):
    """El escaneo no puede elegir una columna BLOCKED/MASKED como metrica.

    Se ejecuta el endpoint real con la cuenta demo `ti` (rol no admin) y se
    controlan las tres fronteras: la introspeccion (que columnas hay), la matriz
    RBAC (que columnas puede ver) y la ejecucion (que SQL se manda).
    """

    TABLE = "fact_ventas"
    COLS = [
        # Primera numerica y MASKED: antes era `num_cols[0]` y se escaneaba.
        {"name": "saldo_masked", "type": "numeric(12,2)", "is_pk": False},
        # BLOCKED: tampoco puede ser metrica.
        {"name": "sueldo_mensual", "type": "numeric(12,2)", "is_pk": False},
        # Unica metrica autorizada.
        {"name": "monto_total", "type": "numeric(12,2)", "is_pk": False},
    ]
    BLOCKED = {"sueldo_mensual", "tarjeta_credito_token"}
    MASKED = {"saldo_masked"}

    def setUp(self):
        from fastapi.testclient import TestClient
        from main import app

        self.client = TestClient(app)
        self.sql_ejecutado = []

    def _token(self):
        resp = self.client.post(
            "/api/v1/auth/login", json={"username": "ti", "password": "ti123"}
        )
        if resp.status_code != 200:
            self.skipTest(f"no se pudo entrar con la cuenta demo ti: {resp.status_code}")
        return resp.json()["access_token"]

    def _escanear(self, cols):
        from app.modules.system import router as system_router

        ejecutados = self.sql_ejecutado

        def _fake_execute(target, sql, dialect="postgres"):
            ejecutados.append(sql)
            # Las filas llevan la clave de la metrica que el endpoint PIDIO. Si
            # eligio una columna sin permiso, la clave de la respuesta lo delata.
            columna = sql.split("SELECT ", 1)[1].split(" FROM ", 1)[0].strip().strip('"')
            valores = [100.0] * 56 + [9000.0] + [50.0] * 3
            return [{columna: v} for v in valores]

        with patch.object(system_router.HealthService, "check_db_connectivity",
                          return_value={"success": True, "message": ""}), \
             patch("app.modules.chat_engine.governance_guard.GovernanceGuard."
                   "get_allowed_tables_for_role", return_value={self.TABLE}), \
             patch("app.modules.chat_engine.governance_guard.GovernanceGuard."
                   "get_blocked_columns_for_role", return_value=set(self.BLOCKED)), \
             patch("app.modules.chat_engine.governance_guard.GovernanceGuard."
                   "get_masked_columns_for_role", return_value=set(self.MASKED)), \
             patch.object(DynamicSchemaPruningService, "get_physical_db_tables",
                          return_value={self.TABLE}), \
             patch.object(DynamicSchemaPruningService, "get_physical_table_columns",
                          return_value=cols), \
             patch("app.modules.chat_engine.sql_executor.SQLExecutor.execute_raw_sql",
                   side_effect=_fake_execute):
            resp = self.client.get(
                "/api/v1/system/anomalies?connection_id=1",
                headers={"Authorization": f"Bearer {self._token()}"},
            )
        self.assertEqual(resp.status_code, 200, resp.text[:300])
        return [a for a in resp.json()["anomalies"] if a.get("type") == "data_quality"]

    def test_la_metrica_sensible_no_se_escanea_ni_se_publica(self):
        anomalias = self._escanear(self.COLS)

        self.assertTrue(self.sql_ejecutado, "el escaneo no corrio: test incompleto")
        self.assertTrue(
            all("monto_total" in sql for sql in self.sql_ejecutado),
            f"el escaneo uso una metrica sin permiso: {self.sql_ejecutado}",
        )
        for sql in self.sql_ejecutado:
            self.assertNotIn("saldo_masked", sql)
            self.assertNotIn("sueldo_mensual", sql)

        payload = str(anomalias)
        self.assertNotIn("saldo_masked", payload)
        self.assertNotIn("sueldo_mensual", payload)
        self.assertTrue(anomalias, "una metrica autorizada con outliers debe seguir reportando")

    def test_sin_metrica_autorizada_no_se_escanea_nada(self):
        """Si todas las numericas estan bloqueadas/enmascaradas, no hay escaneo."""
        cols = [
            {"name": "saldo_masked", "type": "numeric(12,2)", "is_pk": False},
            {"name": "sueldo_mensual", "type": "numeric(12,2)", "is_pk": False},
        ]
        anomalias = self._escanear(cols)
        self.assertEqual(self.sql_ejecutado, [], "el endpoint escaneo sin metrica autorizada")
        self.assertEqual(anomalias, [])


# ---------------------------------------------------------------------------
# 3. `audit_data_quality`: columnas filtradas y SQL por el helper validado
# ---------------------------------------------------------------------------

class TestAuditoriaDeCalidadUsaColumnasYValidador(unittest.TestCase):
    COLS = [
        {"name": "id", "type": "integer", "is_pk": True},
        {"name": "fecha", "type": "date", "is_pk": False},
        {"name": "periodo", "type": "text", "is_pk": False},
        {"name": "tipo", "type": "text", "is_pk": False},
        # BLOCKED: no puede ser metrica, fecha, tipo ni clave de duplicados.
        {"name": "salario", "type": "numeric", "is_pk": False},
        {"name": "rut_dni", "type": "text", "is_pk": False},
        # MASKED: tampoco.
        {"name": "comision", "type": "numeric", "is_pk": False},
        {"name": "monto", "type": "numeric", "is_pk": False},
    ]
    BLOCKED = {"salario", "rut_dni"}
    MASKED = {"comision"}
    SENSIBLES = ("salario", "rut_dni", "comision")

    def _correr(self, guarded=None):
        llamadas = []
        db = MagicMock()
        connection = MagicMock()

        def _fake_guarded(db_, conn_, sql, dialect, user_role, is_admin, role_id,
                          connection_id, table, cols, budget=None):
            llamadas.append({"sql": sql, "cols": [c["name"] for c in cols]})
            if guarded is not None:
                return guarded(sql)
            return [{"n": 0}], sql

        with patch.object(forecast_service, "_load_fact_table",
                          return_value=("fact_ventas", list(self.COLS))), \
             patch.object(forecast_service.GovernanceGuard, "get_blocked_columns_for_role",
                          return_value=set(self.BLOCKED)), \
             patch.object(forecast_service.GovernanceGuard, "get_masked_columns_for_role",
                          return_value=set(self.MASKED)), \
             patch.object(forecast_service, "_run_guarded_sql", side_effect=_fake_guarded), \
             patch.object(forecast_service.SQLExecutor, "execute_raw_sql",
                          side_effect=AssertionError("la auditoria volvio al SQL crudo")):
            findings = forecast_service.audit_data_quality(
                db=db, connection_id=1, user_role="Economista", is_admin=False, role_id=2,
            )
        return findings, llamadas

    def test_ninguna_consulta_toca_columnas_sin_permiso(self):
        findings, llamadas = self._correr()

        self.assertTrue(llamadas, "la auditoria no corrio ningun check: test incompleto")
        for llamada in llamadas:
            for sensible in self.SENSIBLES:
                self.assertNotIn(
                    sensible, llamada["sql"],
                    f"la auditoria consulto la columna sin permiso '{sensible}': {llamada['sql']}",
                )
                self.assertNotIn(sensible, llamada["cols"])

        for finding in findings:
            for sensible in self.SENSIBLES:
                self.assertNotIn(sensible, str(finding))

    def test_la_metrica_elegida_es_la_autorizada(self):
        findings, llamadas = self._correr()
        # `monto` es la unica numerica autorizada y `fecha` la unica de fecha.
        texto = " ".join(c["sql"] for c in llamadas)
        self.assertIn('"monto"', texto)
        self.assertIn('"fecha"', texto)

    def test_un_error_de_validacion_omite_el_check_sin_datos(self):
        """Un rechazo del validador no puede volver al SQL crudo ni filtrar el nombre."""
        def _rechaza(sql):
            raise ForecastUnavailable(
                "La consulta no paso la validacion de seguridad: Columnas bloqueadas: salario"
            )

        findings, llamadas = self._correr(guarded=_rechaza)
        self.assertTrue(llamadas)
        for finding in findings:
            self.assertNotIn("salario", str(finding))
        # La auditoria sigue respondiendo con sus hallazgos estructurales.
        self.assertIsInstance(findings, list)


if __name__ == "__main__":
    unittest.main()

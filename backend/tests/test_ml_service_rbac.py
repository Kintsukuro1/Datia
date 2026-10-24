"""`/ml/projections/run` bajo los MISMOS gates de RBAC que el chat.

Que se verifica aca
-------------------
1. Cuenta sin rol asignado: 403 y el rechazo queda auditado (`RECHAZADO_RBAC`).
2. Columna BLOQUEADA para el rol: como era la unica numerica, la respuesta es
   `available=False` con el motivo de metrica —no un 500 ni numeros—, sin SQL
   ejecutado. Si el rol PUEDE leer otra numerica, la proyeccion sigue y usa esa.
3. Columna ENMASCARADA: `/ml/projections/run` la excluye de las candidatas (una
   metrica tapada no sirve para SUM) y responde sin numeros; `/chat/predict`
   sigue pasando por `mask_rows` y devuelve el valor tapado, sin numeros en
   claro por ninguno de los dos caminos. Este ultimo cubre el defecto que
   respondia 500: `forecast_service._run_guarded_sql` pisaba las filas con el
   contador (int) que devuelve `mask_rows`, que muta las filas in-place.
4. El borde: horizonte fuera de {3, 6, 12} → 422; `null` (Auto) → 200; sin token
   → 401 en los dos endpoints (la estimacion no lee datos, pero tampoco es
   publica).
5. El camino completo sin muros, para que los puntos 2-4 no sean la unica
   evidencia del router: con la serie suficiente se publican los pasos con su
   periodo futuro (cruce de ano incluido) y la plantilla de narrativa; con la
   serie corta no se proyecta pero la serie historica viaja igual.

Por que end-to-end y no con mocks: el defecto del punto 3 vivia en la cadena
router -> service -> ASTValidator -> SQLExecutor -> mask_rows. Los arneses
unitarios no pasan por `mask_rows` y lo habrian dejado pasar.
"""

import unittest
import uuid
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from main import app  # noqa: F401  registra los mappers de SQLAlchemy

# La misma serie de `test_prediction_honesty.py`: 9 meses con el quiebre de mayo.
SERIE = [
    ("2026-01", 9_761_026), ("2026-02", 8_524_173), ("2026-03", 7_319_140),
    ("2026-04", 11_712_796), ("2026-05", 17_599_629), ("2026-06", 20_179_434),
    ("2026-07", 23_380_686), ("2026-08", 20_237_007), ("2026-09", 22_545_032),
]

# Doce meses CERRADOS: con dos grupos alternados, cada uno tiene 6 periodos y
# alcanza el minimo del backtest. La serie de 9 dejaria un grupo con 4.
SERIE_12 = [
    ("2025-10", 10_000), ("2025-11", 11_000), ("2025-12", 12_000),
    ("2026-01", 13_000), ("2026-02", 14_000), ("2026-03", 15_000),
    ("2026-04", 16_000), ("2026-05", 17_000), ("2026-06", 18_000),
    ("2026-07", 19_000), ("2026-08", 20_000), ("2026-09", 21_000),
]

# 24 meses cerrados: cada uno de los dos grupos alternados tiene 12 periodos,
# suficientes para que el backtest deje residuos y el grupo sea publicable.
SERIE_24 = [
    (f"{year}-{month:02d}", 10_000 + index * 1_000)
    for index, (year, month) in enumerate(
        [(2024, 10), (2024, 11), (2024, 12)]
        + [(2025, m) for m in range(1, 13)]
        + [(2026, m) for m in range(1, 10)]
    )
]

# Valores que NO pueden aparecer en el texto de una respuesta enmascarada.
# Se compara contra `resp.text` completo y no contra un campo: una fuga por un
# campo que nadie miro tambien es una fuga.
NUMEROS_EN_CLARO = ("9761026", "8524173", "22545032")


def _llm_caido():
    """Proyeccion con el LLM apagado: el analisis cae a heuristica y la narrativa
    a plantilla. Ningun test de este archivo depende del servidor de inferencia."""
    return patch(
        "app.modules.chat_engine.llm_service.LLMService.generate_completion",
        new=AsyncMock(return_value=None),
    )


class TestProjectionRbacEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from conftest import create_temp_pg_database

        from app.core.database import SessionLocal

        cls.pg_db = create_temp_pg_database("mlrbac")
        cls.client = TestClient(app)
        cls.db = SessionLocal()

    @classmethod
    def tearDownClass(cls):
        from conftest import drop_temp_pg_database

        cls.db.close()
        drop_temp_pg_database(cls.pg_db)

    def setUp(self):
        self.roles = []
        self.users = []
        self.conns = []

    def tearDown(self):
        """La base de metadatos es COMPARTIDA: cada test limpia solo lo suyo.

        El borrado de la conexion va en `finally` a proposito: una corrida que
        falle a mitad de limpieza no puede dejar una CorporateConnection
        huerfana apuntando a una base ya dropeada. Paso de verdad —una corrida
        con el tearDown roto dejo tres y los tests de diccionario/enricher mas
        abajo en la suite empezaron a responder 502 al introspeccionar la
        conexion activa.—
        """
        from app.modules.admin_catalog.models import (
            CorporateConnection,
            RoleColumnPermission,
            RoleTablePermission,
        )
        from app.modules.auth.models import Role, User, UserSession
        from app.modules.telemetry_audit.models import AuditLog

        try:
            for uid in self.users:
                self.db.query(AuditLog).filter(AuditLog.user_id == uid).delete(synchronize_session=False)
                self.db.query(UserSession).filter(UserSession.user_id == uid).delete(synchronize_session=False)
            self.db.query(RoleColumnPermission).filter(
                RoleColumnPermission.role_id.in_(self.roles or [0])
            ).delete(synchronize_session=False)
            self.db.query(RoleTablePermission).filter(
                RoleTablePermission.role_id.in_(self.roles or [0])
            ).delete(synchronize_session=False)
            for uid in self.users:
                self.db.query(User).filter(User.id == uid).delete(synchronize_session=False)
            for rid in self.roles:
                self.db.query(Role).filter(Role.id == rid).delete(synchronize_session=False)
            self.db.commit()
        finally:
            self.db.rollback()
            for cid in self.conns:
                self.db.query(CorporateConnection).filter(CorporateConnection.id == cid).delete(
                    synchronize_session=False
                )
            self.db.commit()

    # --- utilidades -----------------------------------------------------------

    def _tabla(self, filas, extra_numeric=None, dimension=False):
        """Base desechable con la serie pedida, mas la CorporateConnection real.

        `extra_numeric` agrega una segunda columna numerica con los MISMOS
        valores, para probar que bloquear `valor` no deja a la proyeccion sin
        metrica si el rol puede leer otra. `dimension` agrega `complemento`
        TEXT con dos grupos alternados para el desglose.
        """
        from sqlalchemy import text

        from conftest import pg_source_conn_attrs, pg_source_engine

        from app.modules.admin_catalog.models import CorporateConnection

        eng = pg_source_engine(self.pg_db)
        try:
            with eng.begin() as c:
                c.execute(text("DROP TABLE IF EXISTS movimientos"))
                ddl = "CREATE TABLE movimientos (fecha DATE, valor NUMERIC"
                if extra_numeric:
                    ddl += f", {extra_numeric} NUMERIC"
                if dimension:
                    ddl += ", complemento TEXT"
                c.execute(text(ddl + ")"))
                for index, (periodo, valor) in enumerate(filas):
                    columns = ["fecha", "valor"]
                    params = {"f": periodo + "-15", "v": valor}
                    if extra_numeric:
                        columns.append(extra_numeric)
                        params["extra"] = valor
                    if dimension:
                        columns.append("complemento")
                        params["dim"] = "A" if index % 2 == 0 else "B"
                    placeholders = []
                    for column in columns:
                        placeholders.append({
                            "fecha": ":f",
                            "valor": ":v",
                            extra_numeric: ":extra",
                            "complemento": ":dim",
                        }[column])
                    c.execute(
                        text(
                            f"INSERT INTO movimientos ({', '.join(columns)}) "
                            f"VALUES ({', '.join(placeholders)})"
                        ),
                        params,
                    )
        finally:
            eng.dispose()

        conn = CorporateConnection(
            name=f"ML RBAC {uuid.uuid4().hex[:6]}",
            **pg_source_conn_attrs(self.pg_db),
            is_active=False,
            is_uploaded=True,
        )
        self.db.add(conn)
        self.db.commit()
        self.conns.append(conn.id)
        return conn.id

    def _rol(self, conn_id, columna=None, tipo=None):
        from app.modules.admin_catalog.models import RoleColumnPermission, RoleTablePermission
        from app.modules.auth.models import Role

        r = Role(name=f"ML RBAC {uuid.uuid4().hex[:6]}")
        self.db.add(r)
        self.db.commit()
        self.roles.append(r.id)
        self.db.add(RoleTablePermission(
            role_id=r.id, connection_id=conn_id, schema_name="public",
            table_name="movimientos", is_allowed=True, granted_by_admin=True,
        ))
        if columna:
            self.db.add(RoleColumnPermission(
                role_id=r.id, connection_id=conn_id, schema_name="public",
                table_name="movimientos", column_name=columna, permission_type=tipo,
            ))
        self.db.commit()
        return r

    def _cuenta(self, role):
        """(user_id, headers). Con `role=None`: la cuenta sin rol asignado."""
        from app.modules.auth.models import User

        from conftest import make_auth_headers

        username = f"mlrbac_{uuid.uuid4().hex[:8]}"
        u = User(
            username=username, email=f"{username}@example.com", hashed_password="x",
            is_admin=False, is_active=True, role_id=role.id if role else None,
        )
        self.db.add(u)
        self.db.commit()
        self.users.append(u.id)
        # Sesion viva via helper: el preambulo comun vive en conftest.
        return u.id, make_auth_headers(self.db, username)

    def _correr(self, headers, conn_id, horizon=6, **extra):
        payload = {
            "question": "proyecta los ingresos mensuales",
            "connection_id": conn_id,
            "horizon": horizon,
        }
        payload.update(extra)
        with _llm_caido():
            return self.client.post(
                "/api/v1/ml/projections/run",
                json=payload,
                headers=headers,
            )

    def _auditar(self, user_id, conn_id, snapshot, question="proyeccion auditada"):
        """Inserta el snapshot que dejo una corrida anterior, sin correr el LLM."""
        from app.modules.telemetry_audit.models import AuditLog

        audit = AuditLog(
            user_id=user_id,
            username=f"audit_{uuid.uuid4().hex[:8]}",
            user_role="rol",
            question_prompt=question,
            validation_status="PROYECCION_ML",
            target_database="movimientos",
            result_snapshot=snapshot.model_dump_json(),
        )
        self.db.add(audit)
        self.db.commit()
        return audit.id

    # --- 1. Cuenta sin rol ----------------------------------------------------

    def test_cuenta_sin_rol_no_proyecta_y_queda_auditada(self):
        conn = self._tabla(SERIE)
        user_id, headers = self._cuenta(None)

        resp = self._correr(headers, conn)

        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertIn("rol asignado", resp.text.lower())

        from app.modules.telemetry_audit.models import AuditLog

        audit = (
            self.db.query(AuditLog)
            .filter(AuditLog.user_id == user_id, AuditLog.validation_status == "RECHAZADO_RBAC")
            .first()
        )
        self.assertIsNotNone(audit, "el rechazo por falta de rol tiene que quedar auditado")

    # --- 2. Columna bloqueada -------------------------------------------------

    def test_columna_bloqueada_no_publica_numeros_ni_ejecuta_sql(self):
        from app.modules.admin_catalog.models import ColumnPermissionType

        conn = self._tabla(SERIE)
        role = self._rol(conn, "valor", ColumnPermissionType.BLOCKED)
        _, headers = self._cuenta(role)

        resp = self._correr(headers, conn)

        self.assertEqual(resp.status_code, 200, "el rechazo del validador no es un 500")
        body = resp.json()
        self.assertFalse(body["available"])
        self.assertIn("no tiene columna numerica de monto", body["reason"])
        self.assertEqual(body["steps"], [], "sin numeros publicados no hay pasos")
        self.assertEqual(body["series"], [], "la unica numerica esta bloqueada")
        self.assertIsNone(body["sql"], "sin metrica permitida no hay SQL que ejecutar")
        for claro in NUMEROS_EN_CLARO:
            self.assertNotIn(claro, resp.text)

    def test_si_el_rol_puede_leer_otra_numerica_la_proyeccion_sigue(self):
        from app.modules.admin_catalog.models import ColumnPermissionType

        conn = self._tabla(SERIE, extra_numeric="monto")
        role = self._rol(conn, "valor", ColumnPermissionType.BLOCKED)
        _, headers = self._cuenta(role)

        resp = self._correr(headers, conn)

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["available"], body["reason"])
        self.assertEqual(body["metric_column"], "monto")
        self.assertEqual(body["horizon"], 6)
        self.assertEqual(body["horizon_source"], "pedido")
        self.assertIsNotNone(body["total"])
        self.assertEqual(
            body["total"]["point"], sum(paso["point"] for paso in body["steps"])
        )

    # --- 3. Columna enmascarada (el defecto del contador) ---------------------

    def test_columna_enmascarada_se_excluye_y_el_chat_la_tapa(self):
        from app.modules.admin_catalog.models import ColumnPermissionType

        conn = self._tabla(SERIE)
        role = self._rol(conn, "valor", ColumnPermissionType.MASKED)
        _, headers = self._cuenta(role)

        resp = self._correr(headers, conn)

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertFalse(body["available"])
        self.assertIn("no tiene columna numerica de monto", body["reason"])
        self.assertEqual(body["series"], [], "una metrica enmascarada no se proyecta")
        self.assertIsNone(body["sql"], "sin metrica permitida no hay SQL que ejecutar")
        for claro in NUMEROS_EN_CLARO:
            self.assertNotIn(claro, resp.text, "un valor enmascarado llego en claro por /run")

        # El mismo rol y la misma base por el camino del chat: mismos gates.
        with _llm_caido():
            pred = self.client.post(
                "/api/v1/chat/predict",
                json={"connection_id": conn, "include_retention": False,
                      "include_data_quality": False},
                headers=headers,
            )
        self.assertEqual(pred.status_code, 200, pred.text)
        self.assertFalse(pred.json()["forecast"]["available"])
        for claro in NUMEROS_EN_CLARO:
            self.assertNotIn(claro, pred.text, "un valor enmascarado llego en claro por /predict")

    # --- 4. El borde ----------------------------------------------------------

    def test_horizonte_fuera_de_la_lista_es_422_en_los_dos_endpoints(self):
        conn = self._tabla(SERIE)
        role = self._rol(conn)
        _, headers = self._cuenta(role)

        malo_run = self.client.post(
            "/api/v1/ml/projections/run",
            json={"question": "proyecta", "connection_id": conn, "horizon": 5},
            headers=headers,
        )
        self.assertEqual(malo_run.status_code, 422, malo_run.text)

        malo_est = self.client.post(
            "/api/v1/ml/projections/estimate",
            json={"question": "proyecta", "horizon": 0},
            headers=headers,
        )
        self.assertEqual(malo_est.status_code, 422, malo_est.text)

        for valido in (3, 6, 12):
            ok = self.client.post(
                "/api/v1/ml/projections/estimate",
                json={"question": "proyecta", "horizon": valido},
                headers=headers,
            )
            self.assertEqual(ok.status_code, 200, ok.text)
            self.assertEqual(ok.json()["horizon_used"], valido)

        auto = self.client.post(
            "/api/v1/ml/projections/estimate",
            json={"question": "proyecta", "horizon": None},
            headers=headers,
        )
        self.assertEqual(auto.status_code, 200, auto.text)
        self.assertEqual(auto.json()["horizon_used"], 12, "Auto estima con el mayor")

    def test_sin_token_los_dos_endpoints_son_401(self):
        conn = self._tabla(SERIE)

        run = self.client.post(
            "/api/v1/ml/projections/run",
            json={"question": "proyecta", "connection_id": conn, "horizon": 6},
        )
        self.assertEqual(run.status_code, 401, run.text)

        est = self.client.post(
            "/api/v1/ml/projections/estimate",
            json={"question": "proyecta", "horizon": 6},
        )
        self.assertEqual(est.status_code, 401, est.text)

    # --- 5. El camino completo, sin muros -------------------------------------

    def test_serie_suficiente_proyecta_y_publica_pasos_e2e(self):
        conn = self._tabla(SERIE)
        role = self._rol(conn)
        _, headers = self._cuenta(role)

        resp = self._correr(headers, conn)

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["available"])
        self.assertEqual(body["method"], "naive")
        self.assertIs(body["beats_naive"], False)
        self.assertEqual(body["n_periods"], 9)
        self.assertIs(body["has_gaps"], False)
        self.assertEqual(body["horizon"], 6)
        self.assertEqual(body["horizon_source"], "pedido")
        self.assertEqual(
            [paso["period"] for paso in body["steps"]],
            ["2026-10", "2026-11", "2026-12", "2027-01", "2027-02", "2027-03"],
        )
        self.assertIsNotNone(body["total"])
        self.assertEqual(
            body["total"]["point"], sum(paso["point"] for paso in body["steps"])
        )
        self.assertEqual(body["total"]["measured_steps"], 3)
        self.assertIs(body["total"]["extrapolated"], True)
        self.assertIsNone(body["total"]["mape"])
        self.assertIsNone(body["partial_period_excluded"])
        self.assertEqual(body["future_periods_excluded"], [])
        # El LLM esta apagado en estos tests: la narrativa publicada es la
        # plantilla determinista, y no queda vacia.
        self.assertEqual(body["narration_source"], "plantilla")
        self.assertTrue(body["narration"])

    def test_serie_corta_no_proyecta_pero_la_serie_viaja_por_http(self):
        conn = self._tabla(SERIE[:4])
        role = self._rol(conn)
        _, headers = self._cuenta(role)

        resp = self._correr(headers, conn)

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertFalse(body["available"])
        self.assertIn("hay 4", body["reason"])
        self.assertEqual(len(body["series"]), 4, "la serie viaja para que se vea el motivo")
        self.assertEqual(body["steps"], [])
        self.assertIsNone(body["method"])
        self.assertIsNone(body["total"])
        self.assertEqual(body["horizon_source"], "pedido")
        self.assertEqual(set(body["series"][0]), {"periodo", "valor"})

    # --- 6. Selector manual y desglose ---------------------------------------

    def test_contexto_lista_solo_columnas_permitidas(self):
        conn = self._tabla(SERIE, dimension=True)
        role = self._rol(conn)
        _, headers = self._cuenta(role)

        resp = self.client.get(
            f"/api/v1/ml/projections/context?connection_id={conn}",
            headers=headers,
        )

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["available"], body["reason"])
        self.assertEqual(body["table"], "movimientos")
        self.assertEqual(body["metric_columns"], ["valor"])
        self.assertEqual(body["date_columns"], ["fecha"])
        self.assertEqual(body["dimension_columns"], ["complemento"])
        self.assertEqual(body["connection_id"], conn)

    def test_override_manual_no_llama_al_llm_con_horizonte_explicito(self):
        conn = self._tabla(SERIE, extra_numeric="monto")
        role = self._rol(conn)
        user_id, headers = self._cuenta(role)

        with patch(
            "app.modules.ml_forecast.ml_service._ask_llm_for_columns",
            new=AsyncMock(side_effect=AssertionError("no debe llamar al LLM")),
        ) as analisis:
            resp = self._correr(
                headers, conn, metric_column="monto", date_column="fecha",
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["available"], body["reason"])
        self.assertEqual(body["metric_column"], "monto")
        self.assertEqual(body["analysis_source"], "manual")
        self.assertEqual(body["connection_id"], conn)
        analisis.assert_not_called()
        self.assertIsNotNone(body["total"])
        self.assertTrue(body["audit_log_id"])

    def test_override_invalido_falla_sin_sql_ni_numeros(self):
        conn = self._tabla(SERIE)
        role = self._rol(conn)
        _, headers = self._cuenta(role)

        resp = self._correr(
            headers, conn, metric_column="no_existe", date_column="fecha",
        )

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertFalse(body["available"])
        self.assertIn("no es una metrica numerica permitida", body["reason"])
        self.assertEqual(body["steps"], [])
        self.assertEqual(body["series"], [])
        self.assertIsNone(body["sql"])

    def test_desglose_proyecta_los_grupos_del_top(self):
        conn = self._tabla(SERIE_24, dimension=True)
        role = self._rol(conn)
        _, headers = self._cuenta(role)

        resp = self._correr(headers, conn, dimension_column="complemento")

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["available"], body["reason"])
        breakdown = body["breakdown"]
        self.assertTrue(breakdown["available"], breakdown["reason"])
        self.assertEqual(breakdown["dimension_column"], "complemento")
        self.assertEqual(breakdown["top_n"], 5)
        self.assertEqual(len(breakdown["groups"]), 2)
        self.assertEqual({g["value"] for g in breakdown["groups"]}, {"A", "B"})
        for group in breakdown["groups"]:
            self.assertEqual(group["n_periods"], 12)
            self.assertTrue(group["available"], group["reason"])
            self.assertIsNotNone(group["total"])

    # --- 7. Verificacion contra el dato actual --------------------------------

    def _snapshot(self, conn, periods, points, total_point=300):
        from app.modules.ml_forecast.schemas import ProjectionResponse

        steps = []
        for index, (period, point) in enumerate(zip(periods, points), start=1):
            steps.append({
                "step": index,
                "period": period,
                "point": point,
                "lower": 90,
                "upper": 110,
                "band_pct": 10,
                "mape": 5,
                "n_backtests": 4,
                "reliable": True,
                "extrapolated": False,
            })
        return ProjectionResponse(
            available=True,
            question="proyeccion auditada",
            horizon=len(steps),
            series=[],
            steps=steps,
            total={
                "point": total_point, "lower": 270, "upper": 330,
                "band_pct": 10, "mape": 5, "n_backtests": 4,
                "reliable": True, "extrapolated": False, "measured_steps": len(steps),
            },
            method="naive",
            beats_naive=False,
            mape=5,
            mape_by_method={},
            n_periods=len(steps),
            has_gaps=False,
            income_only=True,
            table="movimientos",
            metric_column="valor",
            date_column="fecha",
            connection_id=conn,
        )

    def test_verificacion_compara_solo_periodos_cerrados(self):
        conn = self._tabla([
            ("2026-01", 100), ("2026-02", 120), ("2026-03", 130),
        ])
        role = self._rol(conn)
        user_id, headers = self._cuenta(role)
        snapshot = self._snapshot(
            conn, ["2026-01", "2026-02", "2026-03"], [100, 100, 100]
        )
        audit_id = self._auditar(user_id, conn, snapshot)

        resp = self.client.get(
            f"/api/v1/ml/projections/{audit_id}/verification", headers=headers,
        )

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["available"], body["reason"])
        self.assertEqual(body["measured_steps"], 3)
        self.assertEqual(body["pending_steps"], 0)
        self.assertEqual(body["missing_steps"], 0)
        self.assertEqual(body["inside_band_steps"], 1)
        self.assertEqual(body["outside_band_steps"], 2)
        self.assertIs(body["steps"][0]["inside_band"], True)
        self.assertIs(body["steps"][1]["inside_band"], False)
        self.assertEqual(body["total"]["actual"], 350.0)
        self.assertEqual(body["total"]["projected"], 300.0)
        self.assertIs(body["total"]["inside_band"], False)
        self.assertEqual(body["total"]["status"], "medido")
        self.assertIn("dato actual", body["data_note"])

        from app.modules.telemetry_audit.models import AuditLog

        verificacion = (
            self.db.query(AuditLog)
            .filter(
                AuditLog.user_id == user_id,
                AuditLog.validation_status == "VERIFICACION_PROYECCION_ML",
            )
            .first()
        )
        self.assertIsNotNone(verificacion, "la verificacion tambien deja rastro")

    def test_verificacion_pendiente_no_inventa_actuales(self):
        conn = self._tabla(SERIE)
        role = self._rol(conn)
        user_id, headers = self._cuenta(role)
        snapshot = self._snapshot(
            conn, ["2026-11", "2026-12", "2027-01"], [100, 100, 100]
        )
        audit_id = self._auditar(user_id, conn, snapshot)

        resp = self.client.get(
            f"/api/v1/ml/projections/{audit_id}/verification", headers=headers,
        )

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["available"], body["reason"])
        self.assertEqual(body["measured_steps"], 0)
        self.assertEqual(body["pending_steps"], 3)
        self.assertIsNone(body["steps"][0]["actual"])
        self.assertEqual(body["total"]["status"], "incompleto")
        self.assertIsNone(body["total"]["actual"])

    def test_historial_y_verificacion_son_solo_del_usuario(self):
        from app.modules.telemetry_audit.models import AuditLog

        conn = self._tabla(SERIE)
        role = self._rol(conn)
        user_id, headers = self._cuenta(role)
        snapshot = self._snapshot(
            conn, ["2026-01", "2026-02", "2026-03"], [100, 100, 100]
        )
        own_id = self._auditar(user_id, conn, snapshot)
        ajeno = AuditLog(
            user_id=None,
            username="otro_usuario",
            user_role="rol",
            question_prompt="proyeccion ajena",
            validation_status="PROYECCION_ML",
            target_database="movimientos",
            result_snapshot=snapshot.model_dump_json(),
        )
        self.db.add(ajeno)
        self.db.commit()
        try:
            historial = self.client.get(
                "/api/v1/ml/projections/history", headers=headers,
            )
            self.assertEqual(historial.status_code, 200, historial.text)
            ids = [item["audit_log_id"] for item in historial.json()["items"]]
            self.assertIn(own_id, ids)
            self.assertNotIn(ajeno.id, ids)

            ajeno_resp = self.client.get(
                f"/api/v1/ml/projections/{ajeno.id}/verification", headers=headers,
            )
            self.assertEqual(ajeno_resp.status_code, 404, ajeno_resp.text)
        finally:
            self.db.query(AuditLog).filter(AuditLog.id == ajeno.id).delete(
                synchronize_session=False
            )
            self.db.commit()


if __name__ == "__main__":
    unittest.main()

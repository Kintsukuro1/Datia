"""`/ml/projections/run` bajo los MISMOS gates de RBAC que el chat.

Que se verifica aca
-------------------
1. Cuenta sin rol asignado: 403 y el rechazo queda auditado (`RECHAZADO_RBAC`).
2. Columna BLOQUEADA para el rol: la respuesta es `available=False` con el
   motivo del validador —no un 500 ni numeros— y el SQL intentado viaja en el
   payload, que es el unico registro de lo que la gobernanza nego.
3. Columna ENMASCARADA: el valor tapado llega a la respuesta por
   `/ml/projections/run` Y por `/chat/predict`, y ningun numero en claro aparece
   en el texto de ninguna de las dos. Este caso cubre el defecto que respondia
   500: `forecast_service._run_guarded_sql` pisaba las filas con el contador
   (int) que devuelve `mask_rows`, que muta las filas in-place y cuenta cuantas
   toco.
4. El borde: horizonte fuera de {3, 6, 12} → 422; sin token → 401 en los dos
   endpoints (la estimacion no lee datos, pero tampoco es publica).
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

    def _tabla(self, filas):
        """Base desechable con la serie pedida, mas la CorporateConnection real."""
        from sqlalchemy import text

        from conftest import pg_source_conn_attrs, pg_source_engine

        from app.modules.admin_catalog.models import CorporateConnection

        eng = pg_source_engine(self.pg_db)
        try:
            with eng.begin() as c:
                c.execute(text("DROP TABLE IF EXISTS movimientos"))
                c.execute(text("CREATE TABLE movimientos (fecha DATE, valor NUMERIC)"))
                for periodo, valor in filas:
                    c.execute(
                        text("INSERT INTO movimientos (fecha, valor) VALUES (:f, :v)"),
                        {"f": periodo + "-15", "v": valor},
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

    def _correr(self, headers, conn_id, horizon=6):
        with _llm_caido():
            return self.client.post(
                "/api/v1/ml/projections/run",
                json={
                    "question": "proyecta los ingresos mensuales",
                    "connection_id": conn_id,
                    "horizon": horizon,
                },
                headers=headers,
            )

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

    def test_columna_bloqueada_no_publica_numeros_y_deja_el_sql_intentado(self):
        from app.modules.admin_catalog.models import ColumnPermissionType

        conn = self._tabla(SERIE)
        role = self._rol(conn, "valor", ColumnPermissionType.BLOCKED)
        _, headers = self._cuenta(role)

        resp = self._correr(headers, conn)

        self.assertEqual(resp.status_code, 200, "el rechazo del validador no es un 500")
        body = resp.json()
        self.assertFalse(body["available"])
        self.assertIn("bloquead", (body["reason"] or "").lower())
        self.assertEqual(body["steps"], [], "sin numeros publicados no hay pasos")
        for claro in NUMEROS_EN_CLARO:
            self.assertNotIn(claro, resp.text)
        self.assertIn(
            "SELECT", (body["sql"] or "").upper(),
            "el SQL que se intento leer tiene que viajar para poder auditarlo",
        )

    # --- 3. Columna enmascarada (el defecto del contador) ---------------------

    def test_columna_enmascarada_llega_tapada_y_sin_numeros_en_claro(self):
        from app.modules.admin_catalog.models import ColumnPermissionType

        conn = self._tabla(SERIE)
        role = self._rol(conn, "valor", ColumnPermissionType.MASKED)
        _, headers = self._cuenta(role)

        resp = self._correr(headers, conn)

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertFalse(body["available"])
        self.assertIn("no numericos", body["reason"])
        self.assertEqual(len(body["series"]), 9, "la serie viaja para poder ver el motivo")
        for fila in body["series"]:
            self.assertTrue(str(fila["valor"]).startswith("*"), fila)
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
        self.assertEqual(
            [paso["period"] for paso in body["steps"]],
            ["2026-10", "2026-11", "2026-12", "2027-01", "2027-02", "2027-03"],
        )
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


if __name__ == "__main__":
    unittest.main()

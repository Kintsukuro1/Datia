"""El escaneo de anomalias debe respetar el RBAC y no proyectar columnas sensibles.

Antes de este fix, `/system/anomalies` elegia la primera tabla fact_ alfabetica del
servidor y ejecutaba `SELECT * FROM <tabla> LIMIT 60` via `execute_raw_sql`, sin pasar
por ASTValidator ni por el filtro de columnas. Eso devolvia valores reales de columnas
BLOCKED y MASKED a cualquier usuario autenticado, sin importar su rol, y los metia en
la descripcion de la anomalia.
"""

import unittest
import uuid

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.modules.admin_catalog.models import CorporateConnection
from app.modules.auth.models import User
from app.modules.chat_engine.governance_guard import GovernanceGuard
from app.modules.telemetry_audit.models import AuditLog
from main import app


def _table_from_title(title: str) -> str:
    """Los titulos son 'Anomalía estadística en <tabla>'."""
    return title.rsplit(" ", 1)[-1]


class TestAnomaliesRespectRbac(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()

    def tearDown(self):
        self.db.close()

    def _token(self, username, password):
        resp = self.client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        self.assertEqual(resp.status_code, 200)
        return resp.json()["access_token"]

    def _scan_payload(self, username, password):
        token = self._token(username, password)
        resp = self.client.get(
            "/api/v1/system/anomalies?connection_id=1",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def _scan_for(self, username, password):
        return self._scan_payload(username, password)["anomalies"]

    def test_no_data_anomaly_outside_allowed_tables(self):
        """Ninguna anomalia de calidad de datos puede salir de una tabla prohibida."""
        for username, password in (("ti", "ti123"), ("economista", "economista123")):
            with self.subTest(rol=username):
                user = self.db.query(User).filter(User.username == username).first()
                if user is None:
                    self.skipTest(f"la cuenta demo {username} no existe")

                role_name = user.role.name if user.role else "Usuario"
                allowed = {
                    t.lower()
                    for t in (
                        GovernanceGuard.get_allowed_tables_for_role(
                            role_name, user.is_admin, db=self.db,
                            role_id=user.role_id, connection_id=1,
                        ) or set()
                    )
                }

                data_anomalies = [
                    a for a in self._scan_for(username, password)
                    if a.get("type") == "data_quality"
                ]

                for anomaly in data_anomalies:
                    table = _table_from_title(anomaly["title"]).lower()
                    self.assertIn(
                        table, allowed,
                        f"el rol {username} recibio una anomalia de {table}, "
                        f"fuera de su matriz RBAC {sorted(allowed)}",
                    )

    def test_anomaly_payload_has_no_raw_column_dump(self):
        """La descripcion no debe volcar valores de columnas no numericas.

        Con `SELECT *` la deteccion de outliers recibia todas las columnas y armaba
        descripciones con valores reales de columnas MASKED/BLOCKED. Ahora solo se
        proyecta la metrica numerica, asi que el payload es dimensional y no lleva
        ni RUT ni token ni salario.
        """
        anomalies = self._scan_for("admin", "admin123")
        for anomaly in anomalies:
            if anomaly.get("type") != "data_quality":
                continue
            payload = str(anomaly)
            for leaked in ("tarjeta_credito", "api_key", "rut_dni", "salario", "sueldo"):
                self.assertNotIn(
                    leaked, payload,
                    f"la anomalia expone una columna sensible: {leaked}",
                )

    def test_security_anomalies_only_reach_admin(self):
        """Las anomalias de gobernanza (`type="security"`) son solo del admin.

        Describen tablas sin perfil asignado en el catalogo y bloqueos AST:
        material de administracion. Antes cualquier usuario autenticado recibia
        el conteo y los nombres de las tablas pendientes de clasificar, aunque su
        matriz RBAC no le diera acceso a ninguna de ellas.

        Se inserta un bloqueo reciente para que el tipo "security" exista de
        forma determinista para los dos roles: el corte no se prueba contra una
        lista vacia ni depende de filas de auditoria de otra corrida.
        """
        conn = (
            self.db.query(CorporateConnection)
            .filter(CorporateConnection.id == 1)
            .first()
        )
        if conn is None:
            self.skipTest("la base de pruebas no tiene la conexion 1")

        marker = f"rbac-security-{uuid.uuid4().hex[:8]}"
        try:
            self.db.add(AuditLog(
                username=marker,
                question_prompt="consulta de prueba: anomalias de gobernanza",
                validation_status="RECHAZADO_TABLA_NO_PERMITIDA",
                target_database=conn.database_name,
            ))
            self.db.commit()

            security_admin = [
                a for a in self._scan_for("admin", "admin123")
                if a.get("type") == "security"
            ]
            self.assertTrue(
                security_admin,
                "el admin debe seguir viendo las anomalias de tipo security",
            )

            for username, password in (("ti", "ti123"), ("economista", "economista123")):
                with self.subTest(rol=username):
                    if self.db.query(User).filter(User.username == username).first() is None:
                        self.skipTest(f"la cuenta demo {username} no existe")

                    payload = self._scan_payload(username, password)
                    self.assertEqual(
                        [a for a in payload["anomalies"] if a.get("type") == "security"],
                        [],
                        f"el rol {username} recibio anomalias de gobernanza",
                    )
                    self.assertEqual(
                        payload["count"], len(payload["anomalies"]),
                        "el conteo incluye anomalias que no se devolvieron",
                    )
        finally:
            self.db.query(AuditLog).filter(AuditLog.username == marker).delete(
                synchronize_session=False
            )
            self.db.commit()


if __name__ == "__main__":
    unittest.main()

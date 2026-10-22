"""Los hilos de chat no son accesibles entre usuarios.

Los mensajes de un hilo guardan `data_rows`: filas reales de la base corporativa.
Por eso el aislamiento por `user_id` no es cosmetico.

Vulnerabilidades que cubre:
  1. `GET /threads/shared/{id}` filtraba solo por id: cualquier usuario autenticado
     que conociera un id leia el historial de otro.
  2. `POST /threads` buscaba por id sin `user_id`: se podia sobrescribir el hilo
     ajeno y además reasignarselo (`thread.user_id = current_user.id`).
  3. La vista compartida no puede confiar en `data_rows` ni en los artefactos
     que guarda el dueño: el SQL se re-ejecuta con el RBAC del lector y solo
     se sirve la tabla fresca. Un resumen/grafico sin SQL no se comparte.
"""
import unittest

from fastapi.testclient import TestClient

from main import app  # noqa: F401

from app.core.constants import ROLE_ANALISTA_FINANCIERO, ROLE_INGENIERO_TI
from app.core.database import SessionLocal
from app.modules.auth.models import Role
from app.modules.admin_catalog.models import CorporateConnection
from app.modules.chat_engine.governance_guard import GovernanceGuard
from app.modules.telemetry_audit.models import AuditLog
from sqlalchemy import text


class TestChatThreadIsolation(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()

    def tearDown(self):
        self.db.execute(text("DELETE FROM chat_conversations WHERE id LIKE 'test-idor-%'"))
        self.db.query(AuditLog).filter(
            AuditLog.question_prompt.like("Lectura de hilo compartido 'test-idor-%")
        ).delete(synchronize_session=False)
        self.db.commit()
        self.db.close()

    def _token(self, username, password):
        r = self.client.post("/api/v1/auth/login", json={"username": username, "password": password})
        self.assertEqual(r.status_code, 200, r.text[:200])
        return r.json()["access_token"]

    def _save(self, token, thread_id, **extra):
        payload = {
            "id": thread_id,
            "title": "Consulta de ingresos",
            "connection_id": 1,
            "results": [{"data_rows": [{"saldo": "9999999"}], "summary_text": "secreto"}],
        }
        payload.update(extra)
        return self.client.post(
            "/api/v1/chat/threads", json=payload, headers={"Authorization": f"Bearer {token}"}
        )

    def _demo_connection_id(self):
        conn = (
            self.db.query(CorporateConnection)
            .filter(CorporateConnection.is_uploaded == False)  # noqa: E712
            .first()
        )
        self.assertIsNotNone(conn, "la demo tiene que tener el conector corporativo")
        return conn.id

    def _allowed_tables(self, role_name, connection_id):
        role = self.db.query(Role).filter(Role.name == role_name).first()
        self.assertIsNotNone(role, f"el rol {role_name!r} tiene que existir")
        return GovernanceGuard.get_allowed_tables_for_role(
            role.name, False, db=self.db, role_id=role.id, connection_id=connection_id,
        )

    def test_private_thread_is_not_readable_by_another_user(self):
        owner = self._token("economista", "economista123")
        other = self._token("ti", "ti123")

        created = self._save(owner, "test-idor-privado")
        self.assertEqual(created.status_code, 200, created.text[:200])

        # El propietario sí lo ve por su endpoint normal.
        mine = self.client.get(
            "/api/v1/chat/threads/test-idor-privado", headers={"Authorization": f"Bearer {owner}"}
        )
        self.assertEqual(mine.status_code, 200)

        # Un tercero no, ni por /threads ni por /threads/shared.
        for path in ("/api/v1/chat/threads/test-idor-privado",
                     "/api/v1/chat/threads/shared/test-idor-privado"):
            with self.subTest(path=path):
                r = self.client.get(path, headers={"Authorization": f"Bearer {other}"})
                self.assertEqual(r.status_code, 404, f"{path} devolvio {r.status_code}")
                self.assertNotIn("9999999", r.text, "se filtered el contenido de otro usuario")

    def test_shared_thread_without_traceability_is_denied(self):
        """Un hilo compartido con filas pero sin SQL no se puede revalidar.

        El dueño podía guardar `data_rows` arbitrarios; antes el endpoint los
        servía a cualquier autenticado que conociera el id. Fail-closed: sin
        `traceability.sql_executed` no hay forma de probar que el lector tiene
        acceso, así que devuelve 403 y no filtra las filas.
        """
        owner = self._token("economista", "economista123")
        other = self._token("ti", "ti123")

        self.assertEqual(self._save(owner, "test-idor-compartido", is_shared=True).status_code, 200)

        r = self.client.get(
            "/api/v1/chat/threads/shared/test-idor-compartido",
            headers={"Authorization": f"Bearer {other}"},
        )
        self.assertEqual(r.status_code, 403, r.text[:300])
        self.assertNotIn("9999999", r.text)

    def test_shared_thread_without_sql_is_denied_even_without_rows(self):
        """Un resumen o gráfico sin SQL no se puede regenerar ni revalidar."""
        owner = self._token("admin", "admin123")
        reader = self._token("felipe_economista", "economista123")
        connection_id = self._demo_connection_id()
        raw_rut = "12.345.678-9"

        created = self._save(
            owner,
            "test-idor-shared-no-sql",
            connection_id=connection_id,
            is_shared=True,
            results=[{
                "summary_text": f"El RUT es {raw_rut}",
                "chart_option": {"series": [{"data": [raw_rut]}]},
                "question": f"¿Cuál es el RUT {raw_rut}?",
            }],
        )
        self.assertEqual(created.status_code, 200, created.text[:300])

        r = self.client.get(
            "/api/v1/chat/threads/shared/test-idor-shared-no-sql",
            headers={"Authorization": f"Bearer {reader}"},
        )
        self.assertEqual(r.status_code, 403, r.text[:300])
        self.assertNotIn(raw_rut, r.text)

    def test_shared_thread_with_table_outside_reader_rbac_is_denied(self):
        """Compartir no es una excepción a la gobernanza: se revalida el SQL."""
        owner = self._token("economista", "economista123")
        other = self._token("ti", "ti123")
        connection_id = self._demo_connection_id()

        econ_allowed = self._allowed_tables(ROLE_ANALISTA_FINANCIERO, connection_id)
        ti_allowed = self._allowed_tables(ROLE_INGENIERO_TI, connection_id)
        forbidden = econ_allowed - ti_allowed
        self.assertTrue(forbidden, "la matriz demo tiene que separar finanzas de TI")
        table = sorted(forbidden)[0]
        sql = f"SELECT 1 AS ok FROM {table} LIMIT 1"

        created = self._save(
            owner,
            "test-idor-shared-forbidden",
            connection_id=connection_id,
            is_shared=True,
            results=[{
                "data_rows": [{"ok": 1}],
                "summary_text": "dato de finanzas",
                "traceability": {"sql_executed": sql},
            }],
        )
        self.assertEqual(created.status_code, 200, created.text[:300])

        r = self.client.get(
            "/api/v1/chat/threads/shared/test-idor-shared-forbidden",
            headers={"Authorization": f"Bearer {other}"},
        )
        self.assertEqual(r.status_code, 403, r.text[:300])
        self.assertNotIn('"ok": 1', r.text)

    def test_shared_thread_with_blocked_column_is_denied(self):
        """La tabla puede estar autorizada y la columna no: BLOCKED tambien corta.

        Sin pasar `blocked_columns` al revalidar, este hilo se servia aunque el
        lector tuviera el token de tarjeta bloqueado: el chequeo de tablas solo
        no cubre CLS.
        """
        owner = self._token("economista", "economista123")
        reader = self._token("felipe_economista", "economista123")
        connection_id = self._demo_connection_id()
        sql = "SELECT tarjeta_credito_token FROM dim_clientes LIMIT 1"

        created = self._save(
            owner,
            "test-idor-shared-blocked-column",
            connection_id=connection_id,
            is_shared=True,
            results=[{
                "data_rows": [{"tarjeta_credito_token": "4111-1111-1111-1111"}],
                "summary_text": "token de tarjeta",
                "traceability": {"sql_executed": sql},
            }],
        )
        self.assertEqual(created.status_code, 200, created.text[:300])

        r = self.client.get(
            "/api/v1/chat/threads/shared/test-idor-shared-blocked-column",
            headers={"Authorization": f"Bearer {reader}"},
        )
        self.assertEqual(r.status_code, 403, r.text[:300])
        self.assertNotIn("4111-1111-1111-1111", r.text)

    def test_shared_thread_rebuilds_rows_and_ignores_client_data(self):
        """La vista compartida no confía en `results`: re-ejecuta el SQL.

        El cliente puede enviar `data_rows` con una clave BLOCKED, un alias de
        MASKED o artefactos con datos. Nada de eso se sirve: el lector recibe
        las filas frescas de su propia matriz RBAC.
        """
        owner = self._token("ti", "ti123")
        reader = self._token("juan_ti", "ti123")
        connection_id = self._demo_connection_id()
        raw_rut = "12.345.678-9"
        raw_tarjeta = "4111-1111-1111-1111"

        created = self._save(
            owner,
            "test-idor-shared-rebuild",
            title=f"Consulta TI con {raw_rut}",
            connection_id=connection_id,
            is_shared=True,
            results=[{
                "data_rows": [
                    {"ok": 999},
                    {"tarjeta_credito_token": raw_tarjeta},
                    {"identificador": raw_rut},
                ],
                "question": f"¿Cuál es el RUT {raw_rut}?",
                "summary_text": f"El RUT es {raw_rut}",
                "chart_type": "bar",
                "chart_option": {"series": [{"data": [raw_rut]}]},
                "traceability": {"sql_executed": "SELECT 1 AS ok"},
            }],
        )
        self.assertEqual(created.status_code, 200, created.text[:300])

        r = self.client.get(
            "/api/v1/chat/threads/shared/test-idor-shared-rebuild",
            headers={"Authorization": f"Bearer {reader}"},
        )
        self.assertEqual(r.status_code, 200, r.text[:300])
        body = r.json()
        result = body["results"][0]

        self.assertEqual(result["data_rows"], [{"ok": 1}])
        self.assertEqual(result["data_columns"], ["ok"])
        self.assertEqual(body["title"], "Hilo compartido")
        self.assertEqual(result["question"], "Resultado compartido")
        self.assertIn("Vista compartida", result["summary_text"])
        self.assertEqual(result["chart_type"], "none")
        self.assertEqual(result["chart_option"], {})
        self.assertEqual(result["traceability"]["sql_executed"], "")
        self.assertNotIn(raw_rut, r.text)
        self.assertNotIn(raw_tarjeta, r.text)

    def test_shared_thread_remasks_columns_for_reader(self):
        """El SQL se re-ejecuta y las filas frescas se enmascaran para el lector."""
        owner = self._token("admin", "admin123")
        reader = self._token("felipe_economista", "economista123")
        connection_id = self._demo_connection_id()
        sql = "SELECT rut_dni_cliente FROM dim_clientes LIMIT 1"
        raw_rut = "12.345.678-9"

        created = self._save(
            owner,
            "test-idor-shared-masked",
            title=f"Hilo RUT {raw_rut}",
            connection_id=connection_id,
            is_shared=True,
            results=[{
                "data_rows": [{"rut_dni_cliente": raw_rut}],
                "question": f"¿Cuál es el RUT {raw_rut}?",
                "summary_text": f"El RUT del cliente es {raw_rut}",
                "executive_report": {"summary": f"RUT {raw_rut}"},
                "kpis": [{"title": "RUT", "value": raw_rut}],
                "chart_type": "bar",
                "chart_option": {"series": [{"data": [raw_rut]}]},
                "conversational_response": f"El RUT es {raw_rut}",
                "grounding_info": f"dim_clientes: {raw_rut}",
                "sql_explanation": f"Filtra por {raw_rut}",
                "presentation_hints": {
                    "show_executive_report": True,
                    "show_kpis": True,
                    "show_chart": True,
                },
                "traceability": {
                    "sql_executed": sql,
                    "explanation": f"Filtra por {raw_rut}",
                },
            }],
        )
        self.assertEqual(created.status_code, 200, created.text[:300])

        r = self.client.get(
            "/api/v1/chat/threads/shared/test-idor-shared-masked",
            headers={"Authorization": f"Bearer {reader}"},
        )
        self.assertEqual(r.status_code, 200, r.text[:300])
        body = r.json()["results"][0]
        masked = body["data_rows"][0]["rut_dni_cliente"]
        self.assertNotEqual(masked, raw_rut)
        self.assertIn("*", masked)
        # Los artefactos del cliente no pueden sobrevivir.
        self.assertNotIn(raw_rut, r.text)
        self.assertEqual(body["chart_type"], "none")
        self.assertEqual(body["chart_option"], {})
        self.assertEqual(body["kpis"], [])
        self.assertIsNone(body["executive_report"])
        self.assertIn("Vista compartida", body["summary_text"])
        self.assertEqual(body["question"], "Resultado compartido")
        self.assertEqual(r.json()["title"], "Hilo compartido")
        self.assertEqual(body["traceability"]["sql_executed"], "")
        self.assertFalse(body["presentation_hints"]["show_chart"])

    def test_shared_thread_masks_alias_of_masked_column(self):
        """`SELECT rut_dni_cliente AS identificador` no puede evadir el masking."""
        owner = self._token("admin", "admin123")
        reader = self._token("felipe_economista", "economista123")
        connection_id = self._demo_connection_id()
        sql = "SELECT rut_dni_cliente AS identificador FROM dim_clientes LIMIT 1"
        raw_rut = "12.345.678-9"

        created = self._save(
            owner,
            "test-idor-shared-alias",
            title=f"Hilo RUT {raw_rut}",
            connection_id=connection_id,
            is_shared=True,
            results=[{
                "data_rows": [{"identificador": raw_rut}],
                "question": f"¿Cuál es el RUT {raw_rut}?",
                "summary_text": f"El RUT es {raw_rut}",
                "chart_option": {"series": [{"data": [raw_rut]}]},
                "traceability": {"sql_executed": sql},
            }],
        )
        self.assertEqual(created.status_code, 200, created.text[:300])

        r = self.client.get(
            "/api/v1/chat/threads/shared/test-idor-shared-alias",
            headers={"Authorization": f"Bearer {reader}"},
        )
        self.assertEqual(r.status_code, 200, r.text[:300])
        body = r.json()["results"][0]
        masked = body["data_rows"][0]["identificador"]
        self.assertNotEqual(masked, raw_rut)
        self.assertIn("*", masked)
        self.assertNotIn(raw_rut, r.text)
        self.assertEqual(body["chart_option"], {})
        self.assertEqual(body["question"], "Resultado compartido")
        self.assertEqual(r.json()["title"], "Hilo compartido")

    def test_shared_thread_does_not_serve_owner_text_from_other_results(self):
        """Un seguimiento benigno no puede repetir el valor de una consulta MASKED."""
        owner = self._token("admin", "admin123")
        reader = self._token("felipe_economista", "economista123")
        connection_id = self._demo_connection_id()
        raw_rut = "12.345.678-9"

        created = self._save(
            owner,
            "test-idor-shared-followup",
            title=f"Hilo RUT {raw_rut}",
            connection_id=connection_id,
            is_shared=True,
            results=[
                {
                    "data_rows": [{"rut_dni_cliente": raw_rut}],
                    "question": f"¿Cuál es el RUT {raw_rut}?",
                    "traceability": {
                        "sql_executed": "SELECT rut_dni_cliente FROM dim_clientes LIMIT 1",
                    },
                },
                {
                    "data_rows": [{"ok": 1}],
                    "question": f"y el saldo del RUT {raw_rut}",
                    "traceability": {"sql_executed": "SELECT 1 AS ok"},
                },
            ],
        )
        self.assertEqual(created.status_code, 200, created.text[:300])

        r = self.client.get(
            "/api/v1/chat/threads/shared/test-idor-shared-followup",
            headers={"Authorization": f"Bearer {reader}"},
        )
        self.assertEqual(r.status_code, 200, r.text[:300])
        body = r.json()
        self.assertEqual(body["title"], "Hilo compartido")
        self.assertEqual(
            [result["question"] for result in body["results"]],
            ["Resultado compartido", "Resultado compartido"],
        )
        self.assertNotIn(raw_rut, r.text)

    def test_shared_thread_ignores_malformed_client_rows(self):
        """Las filas del cliente no se usan: un formato raro no se sirve ni rompe."""
        owner = self._token("admin", "admin123")
        reader = self._token("felipe_economista", "economista123")
        connection_id = self._demo_connection_id()
        raw_rut = "12.345.678-9"

        created = self._save(
            owner,
            "test-idor-shared-unverifiable",
            connection_id=connection_id,
            is_shared=True,
            results=[{
                "data_rows": {"rut_dni_cliente": raw_rut},
                "traceability": {"sql_executed": "SELECT 1 AS ok"},
            }],
        )
        self.assertEqual(created.status_code, 200, created.text[:300])

        r = self.client.get(
            "/api/v1/chat/threads/shared/test-idor-shared-unverifiable",
            headers={"Authorization": f"Bearer {reader}"},
        )
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertEqual(r.json()["results"][0]["data_rows"], [{"ok": 1}])
        self.assertNotIn(raw_rut, r.text)

    def test_shared_thread_read_is_audited(self):
        """Abrir un hilo compartido deja huella: se regeneraron datos corporativos.

        Sin este registro, el acceso a la tabla a traves de la vista compartida
        no aparecia en `audit_logs` y no habia evidencia para compliance.
        """
        owner = self._token("admin", "admin123")
        reader = self._token("felipe_economista", "economista123")
        connection_id = self._demo_connection_id()
        thread_id = "test-idor-shared-audit"

        created = self._save(
            owner,
            thread_id,
            connection_id=connection_id,
            is_shared=True,
            results=[{
                "data_rows": [{"ok": 1}],
                "traceability": {"sql_executed": "SELECT 1 AS ok"},
            }],
        )
        self.assertEqual(created.status_code, 200, created.text[:300])

        r = self.client.get(
            f"/api/v1/chat/threads/shared/{thread_id}",
            headers={"Authorization": f"Bearer {reader}"},
        )
        self.assertEqual(r.status_code, 200, r.text[:300])

        audit = (
            self.db.query(AuditLog)
            .filter(AuditLog.question_prompt.like(f"Lectura de hilo compartido '{thread_id}%"))
            .order_by(AuditLog.id.desc())
            .first()
        )
        self.assertIsNotNone(audit, "la lectura compartida no dejo auditoria")
        self.assertEqual(audit.username, "felipe_economista")
        self.assertEqual(audit.validation_status, "HILO_COMPARTIDO_REVALIDADO")
        self.assertEqual(audit.rows_returned, 1)
        self.assertIsNone(audit.sql_generated)
        self.assertEqual(r.json()["results"][0]["audit_log_id"], audit.id)

    def test_cannot_hijack_another_users_thread(self):
        owner = self._token("economista", "economista123")
        attacker = self._token("ti", "ti123")

        self.assertEqual(self._save(owner, "test-idor-sequestro").status_code, 200)

        # El atacante envia el id ajeno: no debe poder sobreescribirlo ni quedárselo.
        hijack = self._save(attacker, "test-idor-sequestro", title="PWN")
        self.assertIn(
            hijack.status_code, (200, 404, 409),
            f"la API respondio {hijack.status_code}: {hijack.text[:200]}",
        )

        owner_view = self.client.get(
            "/api/v1/chat/threads/test-idor-sequestro", headers={"Authorization": f"Bearer {owner}"}
        )
        self.assertEqual(owner_view.status_code, 200)
        self.assertEqual(
            owner_view.json()["title"], "Consulta de ingresos",
            "un usuario sobreescribio el hilo de otro",
        )

        # Y el hilo sigue siendo del dueño original.
        rows = self.db.execute(text(
            "SELECT user_id FROM chat_conversations WHERE id = 'test-idor-sequestro'"
        )).first()
        owner_id = self.db.execute(text(
            "SELECT id FROM users WHERE username = 'economista'"
        )).first().id
        self.assertEqual(rows.user_id, owner_id, "el atacante se apropio del hilo")


if __name__ == "__main__":
    unittest.main()

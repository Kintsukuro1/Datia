"""GET /nulls-audit no puede confundir "sin nulos" con "no se pudo mirar".

BUG: `NullManagerService.audit_connection_nulls` produce `is_complete`,
`total_unverifiable_columns` y `unverifiable_columns`, pero `NullsAuditResponse`
no los declaraba y FastAPI los borraba del JSON. Una columna restringida
(GRANT de tabla pero no de columna) desaparecia de la respuesta y el endpoint
afirmaba un audit completo sobre una base que no se termino de leer.
"""

import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import text

from conftest import (
    create_temp_pg_database,
    drop_temp_pg_database,
    make_auth_headers,
    pg_source_conn_attrs,
    pg_source_engine,
)

from main import app
from app.core.database import SessionLocal
from app.db.init_db import init_db
from app.modules.admin_catalog.models import CorporateConnection
from app.modules.telemetry_audit.models import AuditLog


class TestNullsAuditApi(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        db = SessionLocal()
        init_db(db)
        db.close()

        cls.pg_db = create_temp_pg_database("nullapi")
        eng = pg_source_engine(cls.pg_db)
        try:
            with eng.begin() as c:
                c.execute(text("CREATE TABLE permisos (id INTEGER PRIMARY KEY, secreto TEXT, visible TEXT)"))
                c.execute(text("INSERT INTO permisos VALUES (1, 'valor', 'ok')"))
                c.execute(text("INSERT INTO permisos VALUES (2, NULL, NULL)"))
        finally:
            eng.dispose()

    @classmethod
    def tearDownClass(cls):
        drop_temp_pg_database(cls.pg_db)

    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()
        self._audit_ids = []

        conn = CorporateConnection(
            name=f"nulls-api-{uuid.uuid4().hex[:8]}",
            **pg_source_conn_attrs(self.pg_db),
            is_active=True,
        )
        self.db.add(conn)
        self.db.commit()
        self.conn_id = conn.id

        # Admin con sesion viva: el preambulo comun vive en conftest.
        self.headers = make_auth_headers(self.db, "admin")

    def tearDown(self):
        self.db.query(CorporateConnection).filter(CorporateConnection.id == self.conn_id).delete()
        for audit_id in self._audit_ids:
            self.db.query(AuditLog).filter(AuditLog.id == audit_id).delete()
        self.db.commit()
        self.db.close()

    def _count_rows(self) -> int:
        eng = pg_source_engine(self.pg_db)
        try:
            with eng.begin() as c:
                return int(c.execute(text("SELECT COUNT(*) FROM permisos")).scalar())
        finally:
            eng.dispose()

    def test_restricted_column_is_reported_as_unverifiable(self):
        from sqlalchemy.engine import Connection as SAConnection
        real_execute = SAConnection.execute

        def flaky_execute(self, statement, *args, **kwargs):
            """La conexion real, salvo la columna restringida: falla como el
            server con GRANT de tabla pero no de columna."""
            if "secreto" in str(statement):
                raise RuntimeError("permission denied for column secreto")
            return real_execute(self, statement, *args, **kwargs)

        headers = self.headers
        with patch.object(SAConnection, "execute", flaky_execute):
            resp = self.client.get(
                f"/api/v1/catalog/connections/{self.conn_id}/nulls-audit",
                headers=headers,
            )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["is_complete"], "un escaneo incompleto fue reportado como completo")
        self.assertGreaterEqual(data["total_unverifiable_columns"], 1)
        unverifiable = [c for t in data["tables"] for c in t["unverifiable_columns"]]
        self.assertIn("secreto", [c["column_name"] for c in unverifiable])
        self.assertTrue(any("permission denied" in c["reason"] for c in unverifiable))

    def test_unreadable_database_is_502_not_a_clean_database(self):
        """La base de la conexion no existe: antes el inspector caia a la base
        demo y el endpoint respondia 200 con has_nulls=false sobre una base que
        no se leyo."""
        missing_db = f"tmp_no_existe_{uuid.uuid4().hex[:8]}"
        broken = CorporateConnection(
            name=f"nulls-api-broken-{uuid.uuid4().hex[:8]}",
            **pg_source_conn_attrs(missing_db),
            is_active=True,
        )
        self.db.add(broken)
        self.db.commit()
        try:
            headers = self.headers
            resp = self.client.get(
                f"/api/v1/catalog/connections/{broken.id}/nulls-audit",
                headers=headers,
            )
        finally:
            self.db.query(CorporateConnection).filter(
                CorporateConnection.id == broken.id
            ).delete()
            self.db.commit()

        self.assertEqual(resp.status_code, 502, resp.text)
        self.assertIn(missing_db, resp.json()["detail"])

    def test_policy_without_confirmation_is_rejected_and_writes_nothing(self):
        """Sin `confirm` server-side no se toca la base: el clic en la UI no viaja."""
        before = self._count_rows()

        resp = self.client.post(
            f"/api/v1/catalog/connections/{self.conn_id}/nulls-policy",
            json={"policy": "delete_rows"},
            headers=self.headers,
        )

        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertIn("confirm", resp.json()["detail"].lower())
        self.assertEqual(self._count_rows(), before, "la base cambio sin confirmacion")

    def test_confirmed_policy_is_applied_and_audited(self):
        """Con confirmacion se aplica (fisico) y la mutacion queda en audit_logs."""
        before = self._count_rows()

        resp = self.client.post(
            f"/api/v1/catalog/connections/{self.conn_id}/nulls-policy",
            json={"policy": "delete_rows", "confirm": True},
            headers=self.headers,
        )

        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "success")
        self.assertEqual(resp.json()["rows_affected"], 1)
        self.assertEqual(self._count_rows(), before - 1)

        log = (
            self.db.query(AuditLog)
            .filter(AuditLog.validation_status == "NULL_POLICY_APLICADA")
            .order_by(AuditLog.id.desc())
            .first()
        )
        self.assertIsNotNone(log, "la remediacion fisica no quedo auditada")
        self._audit_ids.append(log.id)
        self.assertEqual(log.username, "admin")
        self.assertEqual(log.rows_returned, 1)
        self.assertIn("delete_rows", log.question_prompt)


if __name__ == "__main__":
    unittest.main()

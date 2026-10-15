"""GET /nulls-audit no puede confundir "sin nulos" con "no se pudo mirar".

BUG: `NullManagerService.audit_connection_nulls` produce `is_complete`,
`total_unverifiable_columns` y `unverifiable_columns`, pero `NullsAuditResponse`
no los declaraba y FastAPI los borraba del JSON. Una columna restringida
(GRANT de tabla pero no de columna) desaparecia de la respuesta y el endpoint
afirmaba un audit completo sobre una base que no se termino de leer.
"""

import os
import sqlite3
import tempfile
import unittest
import uuid
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from main import app
from app.core.database import SessionLocal
from app.core.security import create_access_token
from app.db.init_db import init_db
from app.modules.admin_catalog.models import CorporateConnection, DatabaseType
from app.modules.auth.models import User, UserSession


class TestNullsAuditApi(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        db = SessionLocal()
        init_db(db)
        db.close()

    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()

        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        c = sqlite3.connect(self.db_path)
        c.execute("CREATE TABLE permisos (id INTEGER PRIMARY KEY, secreto TEXT, visible TEXT)")
        c.execute("INSERT INTO permisos VALUES (1, 'valor', 'ok')")
        c.execute("INSERT INTO permisos VALUES (2, NULL, NULL)")
        c.commit()
        c.close()

        conn = CorporateConnection(
            name=f"nulls-api-{uuid.uuid4().hex[:8]}",
            db_type=DatabaseType.SQLITE,
            host=self.db_path,
            port=0,
            database_name=self.db_path,
            username="admin",
            is_active=True,
        )
        self.db.add(conn)
        self.db.commit()
        self.conn_id = conn.id

        self.admin_user = self.db.query(User).filter(User.username == "admin").first()
        self.admin_jti = str(uuid.uuid4())
        self.admin_token = create_access_token(subject=self.admin_user.id, jti=self.admin_jti)
        self.db.add(UserSession(user_id=self.admin_user.id, jti=self.admin_jti, is_revoked=False))
        self.db.commit()

    def tearDown(self):
        self.db.query(CorporateConnection).filter(CorporateConnection.id == self.conn_id).delete()
        self.db.commit()
        self.db.close()
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def test_restricted_column_is_reported_as_unverifiable(self):
        # El endpoint corre en un threadpool: la conexion real nace en el hilo
        # del test y el cursor se usa en el hilo del handler.
        real_conn = sqlite3.connect(self.db_path, check_same_thread=False)
        real_cursor = real_conn.cursor()

        class _FlakyCursor:
            """Delega en un cursor real pero falla en la columna restringida,
            como el server con GRANT de tabla pero no de columna."""

            def execute(self, sql, *a, **kw):
                if "secreto" in sql:
                    raise sqlite3.OperationalError("permission denied for column secreto")
                return real_cursor.execute(sql, *a, **kw)

            def __getattr__(self, k):
                return getattr(real_cursor, k)

        fake_conn = MagicMock()
        fake_conn.cursor.return_value = _FlakyCursor()
        # `__enter__` debe devolver la MISMA conexion: si no, el `with` del
        # servicio agarra un mock nuevo y la columna restringida nunca falla.
        fake_conn.__enter__.return_value = fake_conn

        headers = {"Authorization": f"Bearer {self.admin_token}"}
        with patch("sqlite3.connect", return_value=fake_conn):
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

        real_conn.close()

    def test_unreadable_sqlite_file_is_502_not_a_clean_database(self):
        """El archivo de la conexion no existe: antes el inspector caia a la
        base demo y el endpoint respondia 200 con has_nulls=false sobre una
        base que no se leyo."""
        missing = self.db_path + ".missing"
        broken = CorporateConnection(
            name=f"nulls-api-broken-{uuid.uuid4().hex[:8]}",
            db_type=DatabaseType.SQLITE,
            host=missing,
            port=0,
            database_name=missing,
            username="admin",
            is_active=True,
        )
        self.db.add(broken)
        self.db.commit()
        try:
            headers = {"Authorization": f"Bearer {self.admin_token}"}
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
        self.assertIn(missing, resp.json()["detail"])


if __name__ == "__main__":
    unittest.main()

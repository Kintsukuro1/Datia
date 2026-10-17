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
    pg_source_conn_attrs,
    pg_source_engine,
)

from main import app
from app.core.database import SessionLocal
from app.core.security import create_access_token
from app.db.init_db import init_db
from app.modules.admin_catalog.models import CorporateConnection
from app.modules.auth.models import User, UserSession


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

        conn = CorporateConnection(
            name=f"nulls-api-{uuid.uuid4().hex[:8]}",
            **pg_source_conn_attrs(self.pg_db),
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

    def test_restricted_column_is_reported_as_unverifiable(self):
        from sqlalchemy.engine import Connection as SAConnection
        real_execute = SAConnection.execute

        def flaky_execute(self, statement, *args, **kwargs):
            """La conexion real, salvo la columna restringida: falla como el
            server con GRANT de tabla pero no de columna."""
            if "secreto" in str(statement):
                raise RuntimeError("permission denied for column secreto")
            return real_execute(self, statement, *args, **kwargs)

        headers = {"Authorization": f"Bearer {self.admin_token}"}
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
        self.assertIn(missing_db, resp.json()["detail"])


if __name__ == "__main__":
    unittest.main()

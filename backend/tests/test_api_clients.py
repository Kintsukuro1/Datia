"""Credenciales de servicio: clave una vez, cuenta de servicio y revocación."""
import unittest
import uuid

from fastapi.testclient import TestClient

from conftest import make_auth_headers

from main import app
from app.core.database import SessionLocal
from app.db.init_db import init_db
from app.modules.auth.models import ApiClient, User, UserSession
from app.modules.telemetry_audit.models import AuditLog


class TestApiClients(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        db = SessionLocal()
        init_db(db)
        db.close()

    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()
        self.prefix = f"probe_api_{uuid.uuid4().hex[:8]}"
        self.name = f"{self.prefix}_cliente"
        self.headers = make_auth_headers(self.db, "admin")

    def tearDown(self):
        clients = self.db.query(ApiClient).filter(ApiClient.name.like(f"{self.prefix}%")).all()
        user_ids = [c.user_id for c in clients]
        for client in clients:
            self.db.delete(client)
        if user_ids:
            self.db.query(UserSession).filter(UserSession.user_id.in_(user_ids)).delete(
                synchronize_session=False
            )
            self.db.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)
        self.db.query(AuditLog).filter(
            AuditLog.question_prompt.like(f"%{self.prefix}%")
        ).delete(synchronize_session=False)
        self.db.commit()
        self.db.close()

    def _create(self, role: str = "Usuario Consultor"):
        return self.client.post(
            "/api/v1/auth/api-clients",
            json={"name": self.name, "role": role},
            headers=self.headers,
        )

    def test_create_returns_key_once_and_key_authenticates_service_user(self):
        res = self._create()
        self.assertEqual(res.status_code, 201, res.text[:500])
        data = res.json()
        self.assertTrue(data["api_key"].startswith("datia_sk_"))
        self.assertTrue(data["username"].startswith("svc_"))
        self.assertEqual(data["role_name"], "Usuario Consultor")

        me = self.client.get(
            "/api/v1/auth/me", headers={"X-API-Key": data["api_key"]}
        )
        self.assertEqual(me.status_code, 200, me.text[:300])
        self.assertEqual(me.json()["username"], data["username"])

    def test_list_does_not_expose_the_key(self):
        created = self._create().json()
        listing = self.client.get("/api/v1/auth/api-clients", headers=self.headers)
        self.assertEqual(listing.status_code, 200)
        row = next(item for item in listing.json() if item["id"] == created["id"])
        self.assertNotIn("api_key", row)
        self.assertNotIn("key_hash", row)

    def test_revoke_invalidates_the_key(self):
        created = self._create().json()
        revoked = self.client.delete(
            f"/api/v1/auth/api-clients/{created['id']}", headers=self.headers
        )
        self.assertEqual(revoked.status_code, 200, revoked.text[:300])
        me = self.client.get(
            "/api/v1/auth/me", headers={"X-API-Key": created["api_key"]}
        )
        self.assertEqual(me.status_code, 401)

    def test_invalid_key_is_rejected(self):
        me = self.client.get(
            "/api/v1/auth/me", headers={"X-API-Key": "datia_sk_no_existe"}
        )
        self.assertEqual(me.status_code, 401)

    def test_non_admin_cannot_create(self):
        headers = make_auth_headers(self.db, "economista")
        res = self.client.post(
            "/api/v1/auth/api-clients",
            json={"name": self.name, "role": "Usuario Consultor"},
            headers=headers,
        )
        self.assertEqual(res.status_code, 403)

    def test_admin_role_is_rejected(self):
        res = self._create(role="Administrador de Plataforma")
        self.assertEqual(res.status_code, 400, res.text[:300])


if __name__ == "__main__":
    unittest.main()

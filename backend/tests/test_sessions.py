import datetime
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
from main import app
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import create_access_token
from app.core.time import utcnow_naive
from app.db.init_db import init_db
from app.modules.auth.models import User, UserSession

class TestSessions(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        db = SessionLocal()
        init_db(db)
        db.close()

    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()

    def tearDown(self):
        self.db.close()

    def test_login_creates_session_and_revocation_invalidates_jwt(self):
        """Logging in creates an active UserSession with jti, and revoking that session rejects subsequent requests with 401."""
        # 1. Login
        login_resp = self.client.post("/api/v1/auth/login", json={
            "username": "admin",
            "password": "admin123"
        })
        self.assertEqual(login_resp.status_code, 200)
        token_data = login_resp.json()
        token = token_data["access_token"]
        user_id = token_data["user"]["id"]

        # 2. Check active session in DB
        active_session = self.db.query(UserSession).filter(
            UserSession.user_id == user_id,
            UserSession.is_revoked == False
        ).order_by(UserSession.id.desc()).first()
        self.assertIsNotNone(active_session)
        self.assertIsNotNone(active_session.jti)

        headers = {"Authorization": f"Bearer {token}"}

        # 3. Request with valid active session succeeds
        me_resp = self.client.get("/api/v1/auth/me", headers=headers)
        self.assertEqual(me_resp.status_code, 200)

        # 4. Revoke single session
        revoke_resp = self.client.post(f"/api/v1/auth/sessions/{active_session.id}/revoke", headers=headers)
        self.assertEqual(revoke_resp.status_code, 200)

        # 5. Subsequent request with revoked session token must return 401
        me_revoked_resp = self.client.get("/api/v1/auth/me", headers=headers)
        self.assertEqual(me_revoked_resp.status_code, 401)
        self.assertIn("revocada", me_revoked_resp.json()["detail"].lower())

    def test_revoke_all_user_sessions(self):
        """Admin can revoke all active sessions for a target user."""
        # 1. Login user
        login_resp = self.client.post("/api/v1/auth/login", json={
            "username": "economista",
            "password": "economista123"
        })
        self.assertEqual(login_resp.status_code, 200)
        econ_token = login_resp.json()["access_token"]
        econ_id = login_resp.json()["user"]["id"]

        # Login admin
        admin_resp = self.client.post("/api/v1/auth/login", json={
            "username": "admin",
            "password": "admin123"
        })
        admin_token = admin_resp.json()["access_token"]

        # Verify economista token works
        me_resp = self.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {econ_token}"})
        self.assertEqual(me_resp.status_code, 200)

        # Admin revokes all sessions for economista
        revoke_all_resp = self.client.post(
            f"/api/v1/auth/users/{econ_id}/revoke-all-sessions",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        self.assertEqual(revoke_all_resp.status_code, 200)

        # Economista request now fails
        me_after_resp = self.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {econ_token}"})
        self.assertEqual(me_after_resp.status_code, 401)

    def test_token_without_session_row_is_rejected(self):
        """Un JWT con JTI pero sin fila de sesión no vale: la sesión es la fuente de verdad."""
        user = self.db.query(User).filter(User.username == "admin").first()
        jti = str(uuid.uuid4())
        token = create_access_token(subject=user.id, jti=jti)
        res = self.client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(res.status_code, 401, res.text[:300])
        self.assertIn("no existe", res.json()["detail"].lower())

    def test_idle_session_is_revoked(self):
        """La sesión se corta por inactividad aunque el JWT siga vigente."""
        user = self.db.query(User).filter(User.username == "admin").first()
        jti = str(uuid.uuid4())
        old = utcnow_naive() - datetime.timedelta(minutes=5)
        session = UserSession(
            user_id=user.id, jti=jti, is_revoked=False,
            created_at=old, last_seen_at=old,
        )
        self.db.add(session)
        self.db.commit()
        token = create_access_token(subject=user.id, jti=jti)

        with patch.object(settings, "SESSION_IDLE_TIMEOUT_MINUTES", 1):
            res = self.client.get(
                "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
            )

        self.assertEqual(res.status_code, 401, res.text[:300])
        self.assertIn("inactividad", res.json()["detail"].lower())
        self.db.expire_all()
        self.assertTrue(
            self.db.query(UserSession).filter(UserSession.jti == jti).one().is_revoked
        )

"""Ciclo de vida de la clave temporal, auditoría del login y revocación del logout.

La cuenta la crea un administrador (`POST /auth/users`) y recibe una clave
temporal generada por el servidor. La cuenta nace con `must_change_password=True`:
el login devuelve token, pero `get_current_user` bloquea todo salvo `/auth/me`,
`/auth/change-password` y `/auth/logout` hasta que la cambie. Además la temporal
vence a los `TEMP_PASSWORD_TTL_DAYS` días aunque nunca se haya cambiado.

También cubre que el logout revoque la sesión en el servidor: antes solo borraba
el token del navegador y el JWT seguía siendo válido hasta 24 h.
"""
import datetime
import unittest
import uuid

from fastapi.testclient import TestClient

from main import app
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import verify_password
from app.core.time import utcnow_naive
from app.db.init_db import init_db
from app.modules.auth.models import User, UserSession
from app.modules.telemetry_audit.models import AuditLog

from conftest import make_auth_headers

PREFIX = "tmpauth_"


class TestTemporaryPasswordLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db = SessionLocal()
        init_db(db)
        db.close()

    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()
        self.admin_headers = make_auth_headers(self.db, "admin")

    def tearDown(self):
        try:
            ids = [
                row.id for row in self.db.query(User.id)
                .filter(User.username.like(f"{PREFIX}%")).all()
            ]
            if ids:
                self.db.query(AuditLog).filter(AuditLog.user_id.in_(ids)).delete(
                    synchronize_session=False
                )
                self.db.query(UserSession).filter(UserSession.user_id.in_(ids)).delete(
                    synchronize_session=False
                )
                self.db.query(User).filter(User.id.in_(ids)).delete(synchronize_session=False)
            # Intentos de login con usernames que nunca llegaron a ser cuentas.
            self.db.query(AuditLog).filter(AuditLog.username.like(f"{PREFIX}%")).delete(
                synchronize_session=False
            )
            self.db.commit()
        finally:
            self.db.close()

    # --- helpers ----------------------------------------------------------

    def _create(self):
        suffix = uuid.uuid4().hex[:8]
        username = f"{PREFIX}{suffix}"
        res = self.client.post(
            "/api/v1/auth/users",
            json={"username": username, "email": f"{username}@example.com"},
            headers=self.admin_headers,
        )
        self.assertEqual(res.status_code, 201, res.text[:400])
        data = res.json()
        self.assertEqual(data["user"]["username"], username)
        return data

    def _login(self, username, password):
        return self.client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )

    def _auth(self, token):
        return {"Authorization": f"Bearer {token}"}

    # --- tests ------------------------------------------------------------

    def test_account_created_by_admin_forces_password_change(self):
        data = self._create()
        username = data["user"]["username"]
        temp_password = data["temporary_password"]

        # El alta fuerza el cambio: la temporal sirve para entrar, no para operar.
        self.assertTrue(data["user"]["must_change_password"])
        self.assertIsNotNone(data["user"]["temp_password_expires_at"])
        self.assertTrue(temp_password.startswith("Datia-"))
        self.assertIn("primer inicio de sesión", data["message"])

        # Solo el hash queda en la base.
        user = self.db.query(User).filter(User.username == username).first()
        self.assertIsNotNone(user)
        self.assertNotEqual(user.hashed_password, temp_password)
        self.assertTrue(verify_password(temp_password, user.hashed_password))
        self.assertIsNotNone(user.temp_password_issued_at)

        login = self._login(username, temp_password)
        self.assertEqual(login.status_code, 200, login.text[:300])
        token = login.json()["access_token"]

        me = self.client.get("/api/v1/auth/me", headers=self._auth(token))
        self.assertEqual(me.status_code, 200, me.text[:300])
        self.assertTrue(me.json()["must_change_password"])
        self.assertIsNotNone(me.json()["temp_password_expires_at"])

        # Con la temporal intacta, cualquier endpoint de negocio es 403.
        threads = self.client.get("/api/v1/chat/threads", headers=self._auth(token))
        self.assertEqual(threads.status_code, 403, threads.text[:300])
        self.assertIn("cambiar tu contraseña", threads.json()["detail"])

        new_password = "NuevaClaveSegura-123"
        changed = self.client.post(
            "/api/v1/auth/change-password",
            json={"old_password": temp_password, "new_password": new_password},
            headers=self._auth(token),
        )
        self.assertEqual(changed.status_code, 200, changed.text[:300])

        # La misma sesión sigue viva y ahora sí puede operar.
        threads = self.client.get("/api/v1/chat/threads", headers=self._auth(token))
        self.assertEqual(threads.status_code, 200, threads.text[:300])

        self.db.expire_all()
        user = self.db.query(User).filter(User.username == username).first()
        self.assertFalse(user.must_change_password)
        self.assertIsNone(user.temp_password_issued_at)

        self.assertEqual(self._login(username, new_password).status_code, 200)
        self.assertEqual(self._login(username, temp_password).status_code, 401)

    def test_expired_temporary_password_forces_change_without_admin(self):
        data = self._create()
        username = data["user"]["username"]
        temp_password = data["temporary_password"]

        user = self.db.query(User).filter(User.username == username).first()
        user.temp_password_issued_at = (
            utcnow_naive()
            - datetime.timedelta(days=settings.TEMP_PASSWORD_TTL_DAYS + 1)
        )
        self.db.commit()

        # Login con la temporal vencida: entra, para poder cambiarla.
        login = self._login(username, temp_password)
        self.assertEqual(login.status_code, 200, login.text[:300])
        token = login.json()["access_token"]

        blocked = self.client.get("/api/v1/chat/threads", headers=self._auth(token))
        self.assertEqual(blocked.status_code, 403, blocked.text[:300])
        self.assertIn("venció", blocked.json()["detail"])

        # /auth/me sigue disponible para que el front conozca su estado.
        me = self.client.get("/api/v1/auth/me", headers=self._auth(token))
        self.assertEqual(me.status_code, 200, me.text[:300])
        self.assertIsNotNone(me.json()["temp_password_expires_at"])

        # /chat/suggestions es publico y acepta token opcional. Con la temporal
        # vencida no puede seguir devolviendo las tablas sugeridas para su rol:
        # se degrada a la respuesta anonima.
        suggestions = self.client.get("/api/v1/chat/suggestions", headers=self._auth(token))
        self.assertEqual(suggestions.status_code, 200, suggestions.text[:300])
        self.assertIsNone(suggestions.json()["user_role"])
        self.assertIsNone(suggestions.json()["allowed_tables"])

        new_password = "NuevaClaveSegura-123"
        changed = self.client.post(
            "/api/v1/auth/change-password",
            json={"old_password": temp_password, "new_password": new_password},
            headers=self._auth(token),
        )
        self.assertEqual(changed.status_code, 200, changed.text[:300])

        # La misma sesión sigue viva y ahora sí puede operar.
        threads = self.client.get("/api/v1/chat/threads", headers=self._auth(token))
        self.assertEqual(threads.status_code, 200, threads.text[:300])

        self.db.expire_all()
        user = self.db.query(User).filter(User.username == username).first()
        self.assertFalse(user.must_change_password)
        self.assertIsNone(user.temp_password_issued_at)

        self.assertEqual(self._login(username, new_password).status_code, 200)
        self.assertEqual(self._login(username, temp_password).status_code, 401)

    def test_change_password_rejects_reusing_the_temporary_one(self):
        data = self._create()
        username = data["user"]["username"]
        temp_password = data["temporary_password"]
        token = self._login(username, temp_password).json()["access_token"]

        res = self.client.post(
            "/api/v1/auth/change-password",
            json={"old_password": temp_password, "new_password": temp_password},
            headers=self._auth(token),
        )
        self.assertEqual(res.status_code, 400, res.text[:300])

        self.db.expire_all()
        user = self.db.query(User).filter(User.username == username).first()
        self.assertIsNotNone(user.temp_password_issued_at)

    def test_logout_revokes_the_current_session(self):
        data = self._create()
        username = data["user"]["username"]
        token = self._login(username, data["temporary_password"]).json()["access_token"]

        me = self.client.get("/api/v1/auth/me", headers=self._auth(token))
        self.assertEqual(me.status_code, 200)

        logout = self.client.post("/api/v1/auth/logout", headers=self._auth(token))
        self.assertEqual(logout.status_code, 200, logout.text[:300])
        self.assertGreaterEqual(logout.json()["revoked_sessions"], 1)

        after = self.client.get("/api/v1/auth/me", headers=self._auth(token))
        self.assertEqual(after.status_code, 401, after.text[:300])

    def test_login_and_logout_are_audited(self):
        data = self._create()
        username = data["user"]["username"]
        token = self._login(username, data["temporary_password"]).json()["access_token"]

        ok = (
            self.db.query(AuditLog)
            .filter(
                AuditLog.username == username,
                AuditLog.validation_status == "AUTH_LOGIN_OK",
            )
            .first()
        )
        self.assertIsNotNone(ok, "no quedó registro del login exitoso")

        logout = self.client.post("/api/v1/auth/logout", headers=self._auth(token))
        self.assertEqual(logout.status_code, 200, logout.text[:300])

        self.db.expire_all()
        out = (
            self.db.query(AuditLog)
            .filter(
                AuditLog.username == username,
                AuditLog.validation_status == "AUTH_LOGOUT",
            )
            .first()
        )
        self.assertIsNotNone(out, "no quedó registro del logout")
        self.assertIn("1 sesión", out.question_prompt)

    def test_failed_logins_are_audited_without_the_password(self):
        data = self._create()
        username = data["user"]["username"]
        wrong_password = "ClaveIncorrecta-999"
        unknown_name = f"{PREFIX}nadie_{uuid.uuid4().hex[:6]}"
        unknown_password = "OtraClave-999"

        self.assertEqual(self._login(username, wrong_password).status_code, 401)

        unknown = self._login(unknown_name, unknown_password)
        self.assertEqual(unknown.status_code, 401)
        # El mensaje generico anti-enumeracion no cambia.
        self.assertIn("Usuario o contraseña incorrectos", unknown.json()["detail"])

        self.db.expire_all()
        rows = (
            self.db.query(AuditLog)
            .filter(
                AuditLog.validation_status == "AUTH_LOGIN_FALLIDO",
                AuditLog.username.in_([username, unknown_name]),
            )
            .all()
        )
        self.assertEqual({r.username for r in rows}, {username, unknown_name})
        for row in rows:
            # Ni la clave intentada ni su hash pueden quedar en la auditoría.
            self.assertNotIn(wrong_password, row.question_prompt or "")
            self.assertNotIn(unknown_password, row.question_prompt or "")

    def test_non_admin_cannot_create_accounts(self):
        login = self._login("economista", "economista123")
        self.assertEqual(login.status_code, 200, login.text[:300])
        token = login.json()["access_token"]

        username = f"{PREFIX}forbidden_{uuid.uuid4().hex[:6]}"
        res = self.client.post(
            "/api/v1/auth/users",
            json={"username": username},
            headers=self._auth(token),
        )
        self.assertEqual(res.status_code, 403, res.text[:300])
        self.assertIsNone(self.db.query(User).filter(User.username == username).first())


if __name__ == "__main__":
    unittest.main()

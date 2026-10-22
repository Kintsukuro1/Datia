"""Desactivación y reactivación de cuentas vía `PATCH /auth/users/{user_id}`.

Antes no había forma de dar de baja una cuenta: `UserRoleUpdate` no tenía
`is_active` y el único camino era tocar la base a mano. Estos tests fijan el
contrato completo:

  1. desactivar corta el login Y las sesiones abiertas (el JWT no lleva el estado).
  2. reactivar no revive esas sesiones: el usuario vuelve a loguearse.
  3. nadie se desactiva a sí mismo.
  4. el sistema no puede quedarse sin administrador activo.
  5. cada cambio deja auditoría con el antes/después, sin secretos.
  6. un no-admin no puede desactivar a nadie.

Todos los usuarios llevan prefijo `test_deact_` y se borran en tearDown.
"""
import unittest
import uuid

from fastapi.testclient import TestClient

from main import app  # noqa: F401
from app.core.constants import ROLE_ADMINISTRADOR, ROLE_ANALISTA_FINANCIERO
from app.core.database import SessionLocal
from app.core.security import create_access_token, get_password_hash
from app.db.init_db import init_db
from app.modules.auth.models import Role, User, UserSession
from app.modules.telemetry_audit.models import AuditLog

PREFIX = "test_deact_"
PASSWORD = "clave-de-prueba"


class TestAuthDeactivation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        db = SessionLocal()
        init_db(db)
        db.close()

    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()
        self.admin_id = self._make(f"{PREFIX}admin", is_admin=True, role_name=ROLE_ADMINISTRADOR)
        self.plain_id = self._make(f"{PREFIX}plain", is_admin=False, role_name=ROLE_ANALISTA_FINANCIERO)
        self.target_id = self._make(f"{PREFIX}target", is_admin=False, role_name=ROLE_ANALISTA_FINANCIERO)

    def tearDown(self):
        ids = [u.id for u in self.db.query(User).filter(User.username.like(f"{PREFIX}%")).all()]
        if ids:
            self.db.query(AuditLog).filter(AuditLog.user_id.in_(ids)).delete(
                synchronize_session=False
            )
            self.db.query(UserSession).filter(UserSession.user_id.in_(ids)).delete(
                synchronize_session=False
            )
            self.db.query(User).filter(User.id.in_(ids)).delete(synchronize_session=False)
            self.db.commit()
        self.db.close()

    # --- helpers ----------------------------------------------------------

    def _make(self, username, is_admin, role_name):
        user = User(
            username=username,
            email=f"{username}@test.local",
            hashed_password=get_password_hash(PASSWORD),
            is_admin=is_admin,
            is_active=True,
            must_change_password=False,
        )
        if role_name:
            role = self.db.query(Role).filter(Role.name == role_name).first()
            self.assertIsNotNone(role, f"el catalogo no tiene el rol {role_name}")
            user.role_id = role.id
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user.id

    def _headers(self, user_id):
        jti = str(uuid.uuid4())
        self.db.add(UserSession(user_id=user_id, jti=jti, is_revoked=False))
        self.db.commit()
        return {"Authorization": f"Bearer {create_access_token(subject=user_id, jti=jti)}"}

    def _patch(self, target_id, body, as_user_id):
        return self.client.patch(
            f"/api/v1/auth/users/{target_id}",
            json=body,
            headers=self._headers(as_user_id),
        )

    def _login(self, username):
        return self.client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": PASSWORD},
        )

    def _audit(self, status):
        self.db.expire_all()
        return (
            self.db.query(AuditLog)
            .filter(
                AuditLog.validation_status == status,
                AuditLog.question_prompt.like(f"%{PREFIX}target%"),
            )
            .order_by(AuditLog.id.desc())
            .first()
        )

    # --- 1 y 2: baja, sesiones y reactivación ------------------------------

    def test_deactivation_cuts_login_and_sessions_and_reactivation_restores_login(self):
        login = self._login(f"{PREFIX}target")
        self.assertEqual(login.status_code, 200, login.text[:300])
        old_token = {"Authorization": f"Bearer {login.json()['access_token']}"}
        self.assertEqual(
            self.client.get("/api/v1/auth/me", headers=old_token).status_code, 200
        )

        res = self._patch(self.target_id, {"is_active": False}, as_user_id=self.admin_id)
        self.assertEqual(res.status_code, 200, res.text[:300])
        self.assertFalse(res.json()["is_active"])

        # La sesión emitida antes de la baja ya no sirve.
        after = self.client.get("/api/v1/auth/me", headers=old_token)
        self.assertIn(after.status_code, (400, 401), after.text[:300])

        # Tampoco se puede volver a entrar.
        blocked = self._login(f"{PREFIX}target")
        self.assertEqual(blocked.status_code, 400, blocked.text[:300])
        self.assertIn("desactivada", blocked.json()["detail"])

        # Reactivar: el login vuelve, las sesiones viejas NO.
        res = self._patch(self.target_id, {"is_active": True}, as_user_id=self.admin_id)
        self.assertEqual(res.status_code, 200, res.text[:300])
        self.assertTrue(res.json()["is_active"])

        still_dead = self.client.get("/api/v1/auth/me", headers=old_token)
        self.assertEqual(still_dead.status_code, 401, still_dead.text[:300])

        login = self._login(f"{PREFIX}target")
        self.assertEqual(login.status_code, 200, login.text[:300])
        fresh = {"Authorization": f"Bearer {login.json()['access_token']}"}
        self.assertEqual(self.client.get("/api/v1/auth/me", headers=fresh).status_code, 200)

    def test_deactivation_is_audited_with_before_and_after(self):
        self._patch(self.target_id, {"is_active": False}, as_user_id=self.admin_id)

        row = self._audit("AUTH_DESACTIVACION_CUENTA")
        self.assertIsNotNone(row, "la baja no dejo registro de auditoria")
        self.assertIn("is_active: True -> False", row.question_prompt)
        self.assertEqual(row.user_id, self.admin_id)

        self._patch(self.target_id, {"is_active": True}, as_user_id=self.admin_id)

        row = self._audit("AUTH_ACTUALIZACION_USUARIO")
        self.assertIsNotNone(row, "la reactivacion no dejo registro de auditoria")
        self.assertIn("is_active: False -> True", row.question_prompt)
        # Ningún secreto en la evidencia.
        self.assertNotIn(PASSWORD, row.question_prompt)

    def test_role_change_is_audited(self):
        self._patch(
            self.target_id, {"role": ROLE_ADMINISTRADOR}, as_user_id=self.admin_id
        )
        row = self._audit("AUTH_ACTUALIZACION_USUARIO")
        self.assertIsNotNone(row, "el cambio de rol no dejo registro de auditoria")
        self.assertIn(ROLE_ANALISTA_FINANCIERO, row.question_prompt)
        self.assertIn(ROLE_ADMINISTRADOR, row.question_prompt)

    # --- 3: nadie se da de baja a si mismo --------------------------------

    def test_admin_cannot_deactivate_self(self):
        res = self._patch(self.admin_id, {"is_active": False}, as_user_id=self.admin_id)
        self.assertEqual(res.status_code, 400, res.text[:300])
        self.db.expire_all()
        self.assertTrue(
            self.db.query(User).filter(User.id == self.admin_id).first().is_active,
            "el admin se desactivo a si mismo",
        )

    def test_admin_by_catalog_role_can_reactivate_self_without_losing_admin(self):
        """El PATCH no toca el rol: `is_admin=False` no significa perder admin.

        Antes el calculo del estado resultante solo miraba el flag y, sin un
        `role` en el body, un admin por rol de catalogo parecia estar perdiendo
        privilegios. El PATCH de reactivacion se rechazaba con 400.
        """
        role_admin_id = self._make(
            f"{PREFIX}roleadmin", is_admin=False, role_name=ROLE_ADMINISTRADOR
        )

        res = self._patch(role_admin_id, {"is_active": True}, as_user_id=role_admin_id)

        self.assertEqual(res.status_code, 200, res.text[:300])
        self.assertTrue(res.json()["is_active"])
        self.assertFalse(res.json()["is_admin"])
        self.db.expire_all()
        user = self.db.query(User).filter(User.id == role_admin_id).first()
        self.assertEqual(user.role.name, ROLE_ADMINISTRADOR)

    # --- 4: el sistema no se queda sin administrador activo ---------------

    def test_last_active_admin_cannot_be_deactivated(self):
        """Con `is_active=False` sobre uno mismo da 400: la salvaguarda de
        autodesactivación dispara primero. Lo que se fija acá es el resultado
        observable: el sistema nunca pierde a su último administrador activo."""
        demo_admin = self.db.query(User).filter(User.username == "admin").first()
        self.assertIsNotNone(demo_admin, "falta la cuenta demo 'admin'")
        snapshot = (demo_admin.is_active, demo_admin.is_admin)
        try:
            demo_admin.is_active = False
            self.db.commit()

            res = self._patch(self.admin_id, {"is_active": False}, as_user_id=self.admin_id)
            self.assertEqual(res.status_code, 400, res.text[:300])

            self.db.expire_all()
            self.assertTrue(
                self.db.query(User).filter(User.id == self.admin_id).first().is_active,
                "el sistema se quedo sin ningun administrador activo",
            )
        finally:
            demo_admin.is_active, demo_admin.is_admin = snapshot
            self.db.commit()

    # --- 6: lo hace solo un admin -----------------------------------------

    def test_non_admin_cannot_deactivate_anyone(self):
        res = self._patch(self.target_id, {"is_active": False}, as_user_id=self.plain_id)
        self.assertEqual(res.status_code, 403, res.text[:300])
        self.db.expire_all()
        self.assertTrue(
            self.db.query(User).filter(User.id == self.target_id).first().is_active
        )


if __name__ == "__main__":
    unittest.main()

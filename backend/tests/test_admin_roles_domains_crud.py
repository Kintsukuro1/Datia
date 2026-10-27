"""CRUD del catálogo de roles y dominios (`/auth/roles`, `/auth/domains`).

Contrato que fija este archivo:

  1. solo un administrador puede listar y mutar el catálogo;
  2. alta, renombrado y baja, con auditoría en `audit_logs`;
  3. duplicados rechazados sin distinguir mayúsculas;
  4. el rol administrador no se renombra ni se borra: `is_admin_user` lo
     reconoce por nombre exacto;
  5. un rol con cuentas asignadas no se borra, y uno sin cuentas se lleva sus
     permisos de tabla por cascada;
  6. borrar un dominio deja las entradas del catálogo en `domain_id = NULL`,
     no las borra.

Todos los nombres llevan el prefijo `test_catalog_` y se limpian en tearDown.
"""
import unittest
import uuid

from fastapi.testclient import TestClient

from conftest import make_auth_headers
from main import app
from app.core.constants import ROLE_ADMINISTRADOR
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.db.init_db import init_db
from app.modules.admin_catalog.models import (
    CorporateConnection, RoleTablePermission, SemanticCatalog,
)
from app.modules.auth.models import Domain, Role, User, UserSession
from app.modules.telemetry_audit.models import AuditLog

PREFIX = "test_catalog_"
PASSWORD = "clave-de-prueba"


class TestAdminRolesDomainsCrud(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        db = SessionLocal()
        init_db(db)
        db.close()

    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()
        self.admin_headers = make_auth_headers(self.db, "admin")
        self.plain_headers = make_auth_headers(self.db, "economista")

    def tearDown(self):
        self.db.rollback()
        # Las entradas de catálogo de prueba apuntan a un dominio que se borra
        # abajo; se borran primero para no depender del orden del FK.
        self.db.query(SemanticCatalog).filter(
            SemanticCatalog.table_name.like(f"{PREFIX}%")
        ).delete(synchronize_session=False)
        user_ids = [
            u.id for u in self.db.query(User).filter(
                User.username.like(f"{PREFIX}%")
            ).all()
        ]
        if user_ids:
            self.db.query(UserSession).filter(
                UserSession.user_id.in_(user_ids)
            ).delete(synchronize_session=False)
            self.db.query(User).filter(User.id.in_(user_ids)).delete(
                synchronize_session=False
            )
        for role in self.db.query(Role).filter(Role.name.like(f"{PREFIX}%")).all():
            self.db.delete(role)
        self.db.query(Domain).filter(
            Domain.name.like(f"{PREFIX}%")
        ).delete(synchronize_session=False)
        self.db.query(AuditLog).filter(
            AuditLog.question_prompt.like(f"%{PREFIX}%")
        ).delete(synchronize_session=False)
        self.db.commit()
        self.db.close()

    # --- helpers ----------------------------------------------------------

    def _name(self, suffix: str) -> str:
        return f"{PREFIX}{suffix}_{uuid.uuid4().hex[:8]}"

    def _create_role(self, name, description="rol de prueba"):
        res = self.client.post(
            "/api/v1/auth/roles",
            json={"name": name, "description": description},
            headers=self.admin_headers,
        )
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()

    def _make_user(self, role_id):
        user = User(
            username=self._name("user"),
            email=f"{uuid.uuid4().hex[:8]}@test.local",
            hashed_password=get_password_hash(PASSWORD),
            is_admin=False,
            is_active=True,
            must_change_password=False,
            role_id=role_id,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def _platform_connection(self):
        return self.db.query(CorporateConnection).first()

    def _audit(self, status, needle):
        self.db.expire_all()
        return (
            self.db.query(AuditLog)
            .filter(
                AuditLog.validation_status == status,
                AuditLog.question_prompt.like(f"%{needle}%"),
            )
            .order_by(AuditLog.id.desc())
            .first()
        )

    # --- roles ------------------------------------------------------------

    def test_solo_admin_puede_tocar_el_catalogo(self):
        created = self.client.post(
            "/api/v1/auth/roles",
            json={"name": self._name("prohibido")},
            headers=self.plain_headers,
        )
        self.assertEqual(created.status_code, 403)
        self.assertEqual(
            self.client.get("/api/v1/auth/domains", headers=self.plain_headers).status_code,
            403,
        )

    def test_alta_de_rol_lista_y_auditoria(self):
        name = self._name("analista")
        role = self._create_role(name, "Perfil de prueba")

        self.assertEqual(role["name"], name)
        self.assertEqual(role["description"], "Perfil de prueba")
        self.assertEqual(role["user_count"], 0)

        listed = self.client.get("/api/v1/auth/roles", headers=self.admin_headers)
        self.assertEqual(listed.status_code, 200)
        self.assertIn(
            name, [r["name"] for r in listed.json()],
            "El rol recién creado tiene que aparecer en el catálogo.",
        )

        audit = self._audit("AUTH_ROL_CREADO", name)
        self.assertIsNotNone(audit, "El alta tiene que dejar auditoría.")
        self.assertIn(name, audit.question_prompt)

    def test_duplicado_sin_distinguir_mayusculas(self):
        name = self._name("duplicado")
        self._create_role(name)

        res = self.client.post(
            "/api/v1/auth/roles",
            json={"name": name.upper()},
            headers=self.admin_headers,
        )
        self.assertEqual(res.status_code, 409, res.text)
        self.assertIn("Ya existe un rol", res.json()["detail"])

    def test_renombrar_rol(self):
        role = self._create_role(self._name("viejo"))
        new_name = self._name("nuevo")

        res = self.client.patch(
            f"/api/v1/auth/roles/{role['id']}",
            json={"name": new_name, "description": "renombrado"},
            headers=self.admin_headers,
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["name"], new_name)
        self.assertEqual(res.json()["description"], "renombrado")
        self.assertIsNotNone(self._audit("AUTH_ROL_ACTUALIZADO", new_name))

    def test_no_se_renombra_ni_borra_el_rol_administrador(self):
        admin_role = self.db.query(Role).filter(
            Role.name == ROLE_ADMINISTRADOR
        ).first()
        self.assertIsNotNone(admin_role)

        renamed = self.client.patch(
            f"/api/v1/auth/roles/{admin_role.id}",
            json={"name": self._name("admin_falso")},
            headers=self.admin_headers,
        )
        self.assertEqual(renamed.status_code, 409, renamed.text)
        self.assertIn("rol administrador", renamed.json()["detail"])

        deleted = self.client.delete(
            f"/api/v1/auth/roles/{admin_role.id}",
            headers=self.admin_headers,
        )
        self.assertEqual(deleted.status_code, 409, deleted.text)

    def test_no_se_borra_un_rol_con_usuarios_y_si_sin_ellos(self):
        role = self._create_role(self._name("con_gente"))
        user = self._make_user(role["id"])

        blocked = self.client.delete(
            f"/api/v1/auth/roles/{role['id']}",
            headers=self.admin_headers,
        )
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertIn("1 usuario", blocked.json()["detail"])

        # Se reasigna la cuenta y ahora sí se puede borrar. El permiso de tabla
        # que colgaba del rol tiene que caer con él.
        user.role_id = None
        self.db.commit()
        conn = self._platform_connection()
        self.db.add(RoleTablePermission(
            role_id=role["id"],
            connection_id=conn.id,
            table_name=f"{PREFIX}tabla",
            is_allowed=True,
        ))
        self.db.commit()

        deleted = self.client.delete(
            f"/api/v1/auth/roles/{role['id']}",
            headers=self.admin_headers,
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)

        self.db.expire_all()
        self.assertIsNone(
            self.db.query(Role).filter(Role.id == role["id"]).first(),
            "El rol tiene que estar borrado.",
        )
        self.assertEqual(
            self.db.query(RoleTablePermission).filter(
                RoleTablePermission.role_id == role["id"]
            ).count(),
            0,
            "Los permisos de tabla del rol no pueden sobrevivir al rol.",
        )
        self.assertIsNotNone(self._audit("AUTH_ROL_ELIMINADO", role["name"]))

    # --- dominios ---------------------------------------------------------

    def test_crud_de_dominios(self):
        name = self._name("dominio")
        created = self.client.post(
            "/api/v1/auth/domains",
            json={"name": name, "description": "área de prueba"},
            headers=self.admin_headers,
        )
        self.assertEqual(created.status_code, 201, created.text)
        domain = created.json()
        self.assertEqual(domain["catalog_entries_count"], 0)

        duplicate = self.client.post(
            "/api/v1/auth/domains",
            json={"name": name.upper()},
            headers=self.admin_headers,
        )
        self.assertEqual(duplicate.status_code, 409, duplicate.text)

        new_name = self._name("dominio_renombrado")
        updated = self.client.patch(
            f"/api/v1/auth/domains/{domain['id']}",
            json={"name": new_name},
            headers=self.admin_headers,
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["name"], new_name)

        listed = self.client.get("/api/v1/auth/domains", headers=self.admin_headers)
        self.assertIn(new_name, [d["name"] for d in listed.json()])
        self.assertIsNotNone(self._audit("AUTH_DOMINIO_ACTUALIZADO", new_name))

        deleted = self.client.delete(
            f"/api/v1/auth/domains/{domain['id']}",
            headers=self.admin_headers,
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertIsNotNone(self._audit("AUTH_DOMINIO_CREADO", name))

    def test_borrar_dominio_deja_el_catalogo_sin_dominio(self):
        conn = self._platform_connection()
        self.assertIsNotNone(conn, "la demo tiene que traer un conector de plataforma")

        domain = self.client.post(
            "/api/v1/auth/domains",
            json={"name": self._name("dominio_usado")},
            headers=self.admin_headers,
        ).json()

        table_name = f"{PREFIX}ventas_test"
        self.db.add(SemanticCatalog(
            connection_id=conn.id,
            domain_id=domain["id"],
            schema_name="public",
            table_name=table_name,
            column_name=None,
            description="entrada de prueba",
        ))
        self.db.commit()

        listed = self.client.get("/api/v1/auth/domains", headers=self.admin_headers)
        row = next(d for d in listed.json() if d["id"] == domain["id"])
        self.assertEqual(row["catalog_entries_count"], 1)

        deleted = self.client.delete(
            f"/api/v1/auth/domains/{domain['id']}",
            headers=self.admin_headers,
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertIn("1 entrada(s)", deleted.json()["message"])

        self.db.expire_all()
        entry = self.db.query(SemanticCatalog).filter(
            SemanticCatalog.table_name == table_name
        ).first()
        self.assertIsNotNone(entry, "Borrar el dominio NO borra el catálogo.")
        self.assertIsNone(
            entry.domain_id,
            "La entrada del catálogo queda sin dominio (ON DELETE SET NULL).",
        )


if __name__ == "__main__":
    unittest.main()

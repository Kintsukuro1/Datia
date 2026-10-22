import unittest
import uuid
from unittest.mock import patch
from fastapi.testclient import TestClient

from main import app
from app.core.database import SessionLocal
from app.core.constants import ROLE_USUARIO
from app.db.init_db import init_db
from app.modules.auth.models import User
from app.core.config import settings

from conftest import make_auth_headers

class TestChatSecurity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db = SessionLocal()
        init_db(db)
        db.close()

    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()

        # Cuenta demo con sesion viva: el preambulo comun vive en conftest.
        self.headers = make_auth_headers(self.db, "felipe_economista")

    def tearDown(self):
        self.db.close()

    def test_chat_query_requires_authentication(self):
        """Without JWT Authorization header, /chat/query returns 401 Unauthorized."""
        resp = self.client.post("/api/v1/chat/query", json={"question": "Total de ventas", "connection_id": 1})
        self.assertEqual(resp.status_code, 401)

    def test_chat_query_derives_role_from_jwt_ignoring_body(self):
        """
        Even if client sends user_role='Administrador' in body, /chat/query strictly
        derives the role from the JWT session (Economista).
        """
        # User Economista attempts to query TI tables with spoofed body role
        resp = self.client.post(
            "/api/v1/chat/query",
            json={
                "question": "SELECT * FROM dim_servidores",
                "user_role": "Administrador",  # Spoofed attempt
                "connection_id": 1
            },
            headers=self.headers
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        v_status = data.get("traceability", {}).get("validation_status", "")
        # Must be rejected because JWT user is Economista who cannot access dim_servidores (TI domain)
        self.assertTrue("RECHAZADO" in v_status or "ERROR" in v_status)

    def test_query_open_endpoint_is_removed(self):
        """The legacy unauthenticated /chat/query-open endpoint has been completely removed (404)."""
        resp = self.client.post(
            "/api/v1/chat/query-open",
            json={"question": "Total de ventas"}
        )
        self.assertEqual(resp.status_code, 404)

    def test_suggestions_without_auth_returns_generic_no_tables(self):
        """Without authentication token, /chat/suggestions returns generic suggestions and NO allowed_tables."""
        resp = self.client.get("/api/v1/chat/suggestions")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsNone(data.get("allowed_tables"))
        self.assertIsNone(data.get("user_role"))
        self.assertGreaterEqual(len(data.get("suggestions", [])), 2)
        # Ensure no specific internal table names leaked
        suggestions_text = " ".join(data.get("suggestions", []))
        self.assertNotIn("fact_ventas", suggestions_text)
        self.assertNotIn("dim_servidores", suggestions_text)

    def test_suggestions_with_auth_derives_tables_from_jwt_ignoring_query_param(self):
        """
        With authenticated JWT for Economista, /chat/suggestions ignores ?user_role=Administrador
        and returns allowed_tables strictly matching the JWT user role.
        """
        resp = self.client.get("/api/v1/chat/suggestions?connection_id=1&user_role=Administrador", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn(data.get("user_role"), ["Economista", "Analista Financiero & Comercial"])
        allowed_tables = data.get("allowed_tables")
        self.assertIsNotNone(allowed_tables)
        self.assertGreaterEqual(len(allowed_tables), 1)
        self.assertTrue(any(t.lower() in ["fact_ventas", "question", "survey", "answer"] for t in allowed_tables))

    def test_chat_query_with_missing_connection_returns_404(self):
        """Un `connection_id` que no existe es 404 y NO se consulta otra base.

        Antes el motor caia en silencio a la conexion activa: la respuesta
        parecia valida, pero era de OTRA base, con el id pedido en la auditoria.
        """
        resp = self.client.post(
            "/api/v1/chat/query",
            json={"question": "Total de ventas", "connection_id": 999999},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 404)
        self.assertIn("999999", resp.text)

    def test_suggestions_with_missing_connection_returns_404(self):
        """Las sugerencias resuelven la conexion con el mismo criterio que el chat."""
        resp = self.client.get(
            "/api/v1/chat/suggestions?connection_id=999999",
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 404)
        self.assertIn("999999", resp.text)

    def test_public_self_registration_is_disabled(self):
        """Solo un admin crea cuentas: /auth/register ya no existe."""
        username = f"selfreg_{uuid.uuid4().hex[:6]}"
        resp = self.client.post("/api/v1/auth/register", json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "Password123!",
        })
        self.assertIn(resp.status_code, (404, 405), resp.text[:300])
        self.assertIsNone(self.db.query(User).filter(User.username == username).first())

    def test_admin_create_ignores_extra_fields_and_issues_temp_password(self):
        """
        POST /auth/users usa UserAdminCreate: un payload con is_admin=True,
        role_id=1 y password="a" no elige rol ni clave. El servidor genera una
        temporal de alta entropía y la cuenta nace sin privilegios de admin.
        """
        username = f"hacker_{uuid.uuid4().hex[:6]}"
        admin_headers = make_auth_headers(self.db, "admin")
        user_in_db = None
        payload = {
            "username": username,
            "email": f"{username}@example.com",
            "password": "a",
            "is_admin": True,
            "role_id": 1,
        }
        try:
            resp = self.client.post("/api/v1/auth/users", json=payload, headers=admin_headers)
            self.assertEqual(resp.status_code, 201, resp.text[:300])
            data = resp.json()
            created = data["user"]
            self.assertFalse(created["is_admin"])
            self.assertEqual(created.get("role_name"), ROLE_USUARIO)
            # El alta fuerza el cambio: la temporal entra, pero la cuenta no
            # opera hasta cambiarla.
            self.assertTrue(created["must_change_password"])
            self.assertIsNotNone(created["temp_password_expires_at"])

            temp_password = data["temporary_password"]
            self.assertTrue(temp_password.startswith("Datia-"))
            self.assertGreaterEqual(len(temp_password), 24)
            self.assertNotEqual(temp_password, "a")

            # La clave se guarda hasheada, nunca en claro.
            user_in_db = self.db.query(User).filter(User.username == username).first()
            self.assertIsNotNone(user_in_db)
            self.assertNotEqual(user_in_db.hashed_password, temp_password)
            self.assertFalse(user_in_db.is_admin)
            self.assertIsNotNone(user_in_db.temp_password_issued_at)

            # La temporal sirve para entrar; el cambio se exige después del login.
            login = self.client.post("/api/v1/auth/login", json={
                "username": username,
                "password": temp_password,
            })
            self.assertEqual(login.status_code, 200, login.text[:300])
        finally:
            user_in_db = self.db.query(User).filter(User.username == username).first()
            if user_in_db:
                from app.modules.auth.models import UserSession
                self.db.query(UserSession).filter(UserSession.user_id == user_in_db.id).delete()
                self.db.query(User).filter(User.id == user_in_db.id).delete()
                self.db.commit()


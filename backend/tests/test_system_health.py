import unittest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from main import app
from app.core.database import SessionLocal
from app.db.init_db import init_db
from app.modules.admin_catalog.models import CorporateConnection
from app.core.constants import (
    SYSTEM_STATUS_OPERATIONAL,
    SYSTEM_STATUS_CRITICAL,
    SYSTEM_STATUS_DEGRADED
)

from conftest import make_auth_headers

class TestSystemHealth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db = SessionLocal()
        init_db(db)
        db.close()

    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()

        # Admin con sesion viva: el preambulo comun vive en conftest.
        self.headers = make_auth_headers(self.db, "admin")

    def tearDown(self):
        self.db.close()

    @patch("app.modules.system.health_service.HealthService.check_db_connectivity")
    @patch("app.modules.system.health_service.HealthService.check_llm_connectivity", new_callable=AsyncMock)
    def test_system_health_operational(self, mock_check_llm, mock_check_db):
        mock_check_llm.return_value = {
            "success": True,
            "latency_ms": 15,
            "message": "Conectado exitosamente con Ollama.",
            "available_models": ["llama3.2:3b"]
        }
        mock_check_db.return_value = {
            "success": True,
            "latency_ms": 5,
            "message": "Conexión exitosa en modo SOLO LECTURA."
        }

        response = self.client.get("/api/v1/system/health", headers=self.headers)
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertIn("status", data)
        self.assertEqual(data["status"], SYSTEM_STATUS_OPERATIONAL, data)
        self.assertEqual(data["llm_engine"]["status"], SYSTEM_STATUS_OPERATIONAL)
        self.assertEqual(data["metadata_db"]["status"], SYSTEM_STATUS_OPERATIONAL)
        self.assertIn("corporate_connectors", data)

    @patch("app.modules.system.health_service.HealthService.check_db_connectivity")
    @patch("app.modules.system.health_service.HealthService.check_llm_connectivity", new_callable=AsyncMock)
    def test_system_health_with_custom_query_params(self, mock_check_llm, mock_check_db):
        mock_check_llm.return_value = {
            "success": True,
            "latency_ms": 12,
            "message": "Conectado exitosamente.",
            "available_models": ["Qwen/Qwen2.5-Coder-7B-Instruct-GGUF:Q4_K_M"],
            "provider": "llama_cpp"
        }
        mock_check_db.return_value = {
            "success": True,
            "latency_ms": 3,
            "message": "OK"
        }

        url = "/api/v1/system/health?base_url=http:%2F%2F127.0.0.1:8080&provider=llama_cpp&model_name=Qwen%2FQwen2.5-Coder-7B-Instruct-GGUF:Q4_K_M"
        response = self.client.get(url, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], SYSTEM_STATUS_OPERATIONAL)
        self.assertIn("llama.cpp", data["llm_engine"]["name"])

    @patch("app.modules.system.health_service.HealthService.check_llm_connectivity", new_callable=AsyncMock)
    def test_system_health_critical_when_llm_down(self, mock_check_llm):
        mock_check_llm.return_value = {
            "success": False,
            "latency_ms": 0,
            "message": "No se pudo contactar al servidor LLM.",
            "available_models": []
        }

        response = self.client.get("/api/v1/system/health", headers=self.headers)
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertEqual(data["status"], SYSTEM_STATUS_CRITICAL)
        self.assertEqual(data["llm_engine"]["status"], SYSTEM_STATUS_CRITICAL)

    def test_system_health_requires_auth(self):
        response = self.client.get("/api/v1/system/health")
        self.assertEqual(response.status_code, 401)

    @patch("app.modules.system.health_service.HealthService.check_llm_connectivity", new_callable=AsyncMock)
    def test_llm_test_connection_regression(self, mock_check_llm):
        mock_check_llm.return_value = {
            "success": True,
            "latency_ms": 20,
            "message": "Conectado exitosamente con Ollama en http://127.0.0.1:11434.",
            "available_models": ["llama3.2:3b"]
        }

        req_payload = {
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434",
            "model_name": "llama3.2:3b"
        }
        response = self.client.post("/api/v1/llm/test-connection", json=req_payload, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["latency_ms"], 20)
        self.assertIn("llama3.2:3b", data["available_models"])

    @patch("app.modules.system.health_service.HealthService.check_db_connectivity")
    def test_connector_test_regression(self, mock_check_db):
        mock_check_db.return_value = {
            "success": True,
            "latency_ms": 8,
            "message": "Conexión exitosa al puerto 5432 de POSTGRESQL."
        }

        req_payload = {
            "db_type": "postgresql",
            "host": "localhost",
            "port": 5432,
            "database_name": "corp_db",
            "username": "readonly_user",
            "password": "secret_password"
        }
        response = self.client.post("/api/v1/connectors/test", json=req_payload, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["latency_ms"], 8)

    def test_connector_test_without_password_does_not_422(self):
        """El boton 'Probar' de la tarjeta manda el payload SIN password.

        La conexion ya esta registrada, asi que la contraseña vive cifrada en el
        servidor y el cliente no la tiene: el endpoint no puede exigirla. Antes
        `password: str` era obligatorio y la tarjeta recibia un 422, cuyo
        `detail` es un array que el front pintaba y tumbaba la pagina en negro.
        El endpoint no lee la contraseña (solo abre un socket TCP), asi que el
        default vacio no cambia lo que se verifica.
        """
        req_payload = {
            "db_type": "postgresql",
            "host": "127.0.0.1",
            "port": 1,  # puerto cerrado: el socket tiene que fallar rapido
            "database_name": "no_existe",
            "username": "admin"
        }
        response = self.client.post("/api/v1/connectors/test", json=req_payload, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        # El endpoint responde de verdad, no con un error de validacion.
        self.assertIn("success", data)
        self.assertIsInstance(data["message"], str)
        self.assertFalse(data["success"])

    def test_connector_test_requires_admin(self):
        """Un perfil normal no puede hacer que el backend abra sockets a un host
        arbitrario (SSRF hacia la red local). Antes bastaba estar autenticado."""
        user_headers = make_auth_headers(self.db, "felipe_economista")
        payload = {
            "db_type": "postgresql",
            "host": "127.0.0.1",
            "port": 5432,
            "database_name": "corp_db",
            "username": "readonly_user",
        }
        response = self.client.post(
            "/api/v1/connectors/test", json=payload, headers=user_headers
        )
        self.assertEqual(response.status_code, 403, response.text[:300])

        response = self.client.post(
            "/api/v1/connectors/test-metadata-db",
            json={"server": "127.0.0.1", "port": 5432, "db_name": "corp_db"},
            headers=user_headers,
        )
        self.assertEqual(response.status_code, 403, response.text[:300])

    def test_system_anomalies_endpoint(self):
        response = self.client.get("/api/v1/system/anomalies", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("count", data)
        self.assertIn("anomalies", data)
        self.assertIn("has_critical", data)
        self.assertIsInstance(data["anomalies"], list)

    def test_system_anomalies_with_connection_id(self):
        response = self.client.get("/api/v1/system/anomalies?connection_id=1", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("count", data)
        self.assertIn("anomalies", data)
        self.assertIsInstance(data["anomalies"], list)
        for a in data["anomalies"]:
            self.assertIn("id", a)
            self.assertIn("type", a)
            self.assertIn("title", a)
            self.assertIn("description", a)


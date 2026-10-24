"""
Politica LLM de `/system/health`: `LLM_MODE` decide si manda la URL del cliente.

- `central`/`external`: la URL, el modelo y el provider del query string se
  IGNORAN. El panel sondea la configuracion del servidor y no emite ninguna
  request al host que mando el cliente.
- `user`: la empresa habilito el LLM propio por usuario. La URL pasa por
  `validate_llm_base_url` antes de tocar la red; link-local, hostnames internos
  e IPs publicas dan 400 sin request.

La proteccion no se reimplementa: `system/router.py` usa la MISMA
`resolve_user_llm_config` que `/llm/test-*`, el chat y el enriquecimiento.
"""
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

from main import app
from app.core.config import settings
from app.core.database import SessionLocal
from app.db.init_db import init_db
from app.modules.system import router as system_router

from conftest import make_auth_headers


def _no_httpx(*args, **kwargs):
    """Falla si se emite alguna request HTTP durante el test."""
    raise AssertionError("Se emitio una request HTTP a un host no permitido")


class TestSystemHealthSSRF(unittest.TestCase):

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

    def _assert_rejected_in_user_mode(self, base_url):
        with patch.object(settings, "LLM_MODE", "user"), \
             patch("httpx.AsyncClient.get", side_effect=_no_httpx), \
             patch("httpx.AsyncClient.post", side_effect=_no_httpx):
            res = self.client.get(
                "/api/v1/system/health",
                params={"base_url": base_url},
                headers=self.headers,
            )
        self.assertEqual(res.status_code, 400, res.text[:300])
        self.assertIn("no permitido", res.json()["detail"].lower())

    # ------------------------------------------------------------------ BUG
    def test_cloud_metadata_url_is_rejected_in_user_mode(self):
        """169.254.169.254 -> 400 y ni una sola request sale hacia esa IP."""
        self._assert_rejected_in_user_mode("http://169.254.169.254")

    def test_internal_service_host_is_rejected_in_user_mode(self):
        """Nombre interno en vez de IP: mismo ataque, misma puerta."""
        self._assert_rejected_in_user_mode("http://redis-interno:6379")

    def test_userinfo_trick_is_rejected_in_user_mode(self):
        """`http://localhost@169.254.169.254` tiene hostname interno, no localhost."""
        self._assert_rejected_in_user_mode("http://localhost@169.254.169.254")

    def test_base_url_is_ignored_in_central_mode(self):
        """Default de produccion: la URL del cliente se ignora, no se rechaza."""
        captured = {}

        async def fake_check(provider=None, base_url=None, model_name=None,
                             timeout=2.0, trusted=False):
            captured.update(
                base_url=base_url, provider=provider, trusted=trusted,
            )
            return {
                "success": True, "latency_ms": 3,
                "message": "ok", "available_models": ["llama3.2:3b"],
                "active_url": base_url, "provider": provider or "ollama",
            }

        with patch.object(settings, "LLM_MODE", "central"), \
             patch.object(system_router.HealthService, "check_llm_connectivity",
                          new=fake_check), \
             patch.object(system_router.HealthService, "check_db_connectivity", return_value={
                 "success": True, "latency_ms": 1, "message": "ok"}), \
             patch("httpx.AsyncClient.get", side_effect=_no_httpx), \
             patch("httpx.AsyncClient.post", side_effect=_no_httpx):
            res = self.client.get(
                "/api/v1/system/health",
                params={"base_url": "http://169.254.169.254"},
                headers=self.headers,
            )

        self.assertEqual(res.status_code, 200, res.text[:300])
        self.assertEqual(captured["base_url"], settings.OLLAMA_BASE_URL)
        self.assertTrue(captured["trusted"])

    def test_openai_compatible_provider_uses_its_url(self):
        """`LLM_PROVIDER=openai_compatible` no puede seguir sondeando Ollama."""
        captured = {}

        async def fake_check(provider=None, base_url=None, model_name=None,
                             timeout=2.0, trusted=False):
            captured.update(base_url=base_url, provider=provider)
            return {
                "success": True, "latency_ms": 3,
                "message": "ok", "available_models": ["m"],
                "active_url": base_url, "provider": provider or "ollama",
            }

        with patch.object(settings, "LLM_MODE", "central"), \
             patch.object(settings, "LLM_PROVIDER", "openai_compatible"), \
             patch.object(system_router.HealthService, "check_llm_connectivity",
                          new=fake_check), \
             patch.object(system_router.HealthService, "check_db_connectivity", return_value={
                 "success": True, "latency_ms": 1, "message": "ok"}):
            res = self.client.get("/api/v1/system/health", headers=self.headers)

        self.assertEqual(res.status_code, 200, res.text[:300])
        self.assertEqual(captured["base_url"], settings.OPENAI_COMPATIBLE_URL)
        self.assertEqual(captured["provider"], "openai_compatible")

    def test_loopback_base_url_is_used_in_user_mode(self):
        """Con `LLM_MODE=user` un loopback valido se usa de verdad."""
        captured = {}

        async def fake_check(provider=None, base_url=None, model_name=None,
                             timeout=2.0, trusted=False):
            captured.update(base_url=base_url, trusted=trusted)
            return {
                "success": True, "latency_ms": 3,
                "message": "ok", "available_models": ["llama3.2:3b"],
                "active_url": base_url, "provider": provider or "ollama",
            }

        with patch.object(settings, "LLM_MODE", "user"), \
             patch.object(system_router.HealthService, "check_llm_connectivity", new=fake_check), \
             patch.object(system_router.HealthService, "check_db_connectivity", return_value={
                 "success": True, "latency_ms": 1, "message": "ok"}):
            res = self.client.get(
                "/api/v1/system/health",
                params={"base_url": "http://127.0.0.1:8080"},
                headers=self.headers,
            )
        self.assertEqual(res.status_code, 200, res.text[:300])
        self.assertEqual(captured.get("base_url"), "http://127.0.0.1:8080")
        self.assertTrue(captured.get("trusted"))

    def test_health_without_base_url_still_works(self):
        """Sin base_url se usa la config del servidor: no se rompe el panel."""
        with patch.object(system_router.HealthService, "check_llm_connectivity",
                          new=_async_ok()) as mock, \
             patch.object(system_router.HealthService, "check_db_connectivity", return_value={
                 "success": True, "latency_ms": 1, "message": "ok"}):
            res = self.client.get("/api/v1/system/health", headers=self.headers)
        self.assertEqual(res.status_code, 200, res.text[:300])
        self.assertEqual(mock.await_args.kwargs["base_url"], settings.OLLAMA_BASE_URL)
        self.assertTrue(mock.await_args.kwargs["trusted"])

    def test_ssrf_still_requires_authentication(self):
        """La validacion no reemplaza la authz: anonimo sigue siendo 401."""
        with patch("httpx.AsyncClient.get", side_effect=_no_httpx), \
             patch("httpx.AsyncClient.post", side_effect=_no_httpx):
            res = self.client.get("/api/v1/system/health", params={"base_url": "http://169.254.169.254"})
        self.assertEqual(res.status_code, 401)

    # ------------------------------------------------------- causa raiz
    def test_router_reuses_the_shared_policy_resolver(self):
        """Si alguien reintroduce un allowlist propio en system/router, esto falla.
        Las tres entradas (health, test-connection, test-completion) deben pasar por
        la MISMA politica, o la siguiente puerta que se abra repite el error."""
        from app.modules.chat_engine.llm_service import resolve_user_llm_config as shared
        self.assertIs(system_router.resolve_user_llm_config, shared)

    def test_llm_policy_endpoint_reports_the_company_mode(self):
        """El frontend deshabilita Ajustes en central con este endpoint."""
        with patch.object(settings, "LLM_MODE", "central"):
            data = self.client.get(
                "/api/v1/system/llm-policy", headers=self.headers
            ).json()
        self.assertEqual(data["mode"], "central")
        self.assertFalse(data["allow_user_llm"])
        self.assertEqual(data["default_model"], settings.OLLAMA_MODEL)

        with patch.object(settings, "LLM_MODE", "user"):
            data = self.client.get(
                "/api/v1/system/llm-policy", headers=self.headers
            ).json()
        self.assertEqual(data["mode"], "user")
        self.assertTrue(data["allow_user_llm"])

    def test_health_service_never_reports_success_without_verifying(self):
        """Si el LLM no responde NINGUN endpoint debe devolver success:False
        con el motivo, nunca True."""
        import asyncio
        from app.modules.system.health_service import HealthService

        async def boom(*a, **k):
            raise RuntimeError("connection refused")

        with patch("httpx.AsyncClient.get", new=boom), \
             patch("httpx.AsyncClient.post", new=boom):
            res = asyncio.run(HealthService.check_llm_connectivity(
                provider="ollama", base_url="http://127.0.0.1:59999", model_name="m"))

        self.assertFalse(res["success"], "reporto LLM conectado sin haberlo verificado")
        self.assertTrue(res["message"])


def _async_ok():
    from unittest.mock import AsyncMock
    m = AsyncMock(return_value={
        "success": True, "latency_ms": 3, "message": "ok",
        "available_models": [settings.OLLAMA_MODEL],
        "active_url": settings.OLLAMA_BASE_URL, "provider": "ollama",
    })
    return m


if __name__ == "__main__":
    unittest.main()

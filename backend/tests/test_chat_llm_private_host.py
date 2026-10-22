"""
SSRF del chat: el `llm_base_url` que elige el USUARIO solo puede ser loopback.

Antes `/chat/query` y `/chat/query/stream` validaban con `validate_llm_base_url`,
la allowlist del SINK. Esa allowlist acepta RFC1918 y `host.docker.internal`
porque tiene que cubrir la URL configurada por el despliegue (docker-compose usa
`http://host.docker.internal:11434`). Pero aca el valor lo elige cualquier
usuario autenticado: aceptar una IP privada significa que el backend emite el POST
con el prompt a un host que ese usuario controla, y le devuelve la respuesta.

Estos tests fijan:
  1. Una URL privada/interna se rechaza con 400 ANTES de correr el motor y sin
     emitir ninguna request (ni normal ni stream).
  2. Loopback sigue funcionando y es lo unico que llega al ContextVar.
  3. Las dos politicas siguen separadas: el sink (URL del despliegue) no se
     endurecio, el router (URL del usuario) si.
"""
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.db.init_db import init_db
from app.modules.chat_engine import llm_service
from app.modules.chat_engine.llm_diagnostic_router import _resolve_llm_base_url
from app.modules.chat_engine.llm_service import LLMService, validate_llm_base_url
from app.modules.chat_engine.router import _validated_request_llm_base_url
from app.modules.chat_engine.schemas import QueryRequest

from conftest import make_auth_headers

# Hosts que el usuario NO puede elegir. `host.docker.internal` es valido como
# configuracion del servidor (ver test_llm_sink_validation.py) y por eso mismo
# no lo es como valor que manda el cliente.
PRIVATE_URLS = [
    "http://192.168.1.50:11434",
    "http://10.0.0.5",
    "http://172.16.4.2:1234",
    "http://169.254.169.254",
    "http://host.docker.internal:11434",
    "http://redis-interno:6379",
]


def _no_request(*args, **kwargs):
    """Falla si el backend emite cualquier request HTTP."""
    raise AssertionError("Se emitio una request HTTP a un host no permitido")


class TestChatRejectsPrivateHosts(unittest.TestCase):
    """400 antes del motor, sin requests, en los dos endpoints."""

    @classmethod
    def setUpClass(cls):
        db = SessionLocal()
        init_db(db)
        db.close()

    def setUp(self):
        # `raise_server_exceptions=False`: los fakes del motor devuelven None y la
        # request terminaria en 500. Lo que se verifica es que el motor NO corrio.
        self.client = TestClient(app_under_test(), raise_server_exceptions=False)
        self.db = SessionLocal()
        self.headers = make_auth_headers(self.db, "admin")

    def tearDown(self):
        self.db.close()

    def _assert_rejected(self, path, url):
        with patch(
            "app.modules.chat_engine.router.QueryEngine.execute_query",
            new_callable=AsyncMock,
        ) as mock_exec, patch.object(
            LLMService, "generate_completion", new_callable=AsyncMock
        ) as mock_gen, patch.object(
            LLMService, "stream_completion", new_callable=AsyncMock
        ) as mock_stream, patch(
            "httpx.AsyncClient.post", side_effect=_no_request
        ), patch(
            "httpx.AsyncClient.get", side_effect=_no_request
        ), patch(
            "httpx.AsyncClient.stream", side_effect=_no_request
        ):
            resp = self.client.post(
                path,
                json={
                    "question": "ventas totales",
                    "connection_id": 1,
                    "llm_base_url": url,
                },
                headers=self.headers,
            )

        self.assertEqual(resp.status_code, 400, f"{url}: {resp.text[:300]}")
        self.assertIn("no permitido", resp.json()["detail"].lower())
        # Ni el motor ni el sink: el rechazo es en la entrada.
        mock_exec.assert_not_called()
        mock_gen.assert_not_called()
        mock_stream.assert_not_called()
        # Y el stream no se abrio: no hay evento SSE en el cuerpo.
        self.assertNotIn("event:", resp.text)

    def test_query_rejects_private_hosts(self):
        for url in PRIVATE_URLS:
            with self.subTest(url=url):
                self._assert_rejected("/api/v1/chat/query", url)

    def test_query_stream_rejects_private_hosts(self):
        for url in PRIVATE_URLS:
            with self.subTest(url=url):
                self._assert_rejected("/api/v1/chat/query/stream", url)


class TestLoopbackStillWorks(unittest.TestCase):
    """El LLM de la propia maquina sigue siendo una eleccion valida."""

    def test_resolver_accepts_loopback(self):
        for url in ("http://127.0.0.1:11434", "http://localhost:11434",
                    "http://[::1]:11434", "http://127.0.0.1:8080"):
            with self.subTest(url=url):
                self.assertEqual(_resolve_llm_base_url(url), url)

    def test_router_helper_returns_the_validated_url(self):
        for url in ("http://127.0.0.1:11434", "http://[::1]:11434"):
            with self.subTest(url=url):
                out = _validated_request_llm_base_url(
                    QueryRequest(question="q", llm_base_url=url)
                )
                self.assertEqual(out, url)

    def test_router_helper_leaves_absent_config_as_none(self):
        # Sin `llm_base_url` no hay override: manda el servidor configurado.
        self.assertIsNone(_validated_request_llm_base_url(QueryRequest(question="q")))

    def test_loopback_reaches_the_engine_with_the_validated_url(self):
        """Un loopback se acepta y llega al ContextVar del motor (no se rechaza)."""
        from main import app

        client = TestClient(app, raise_server_exceptions=False)
        db = SessionLocal()
        try:
            headers = make_auth_headers(db, "admin")
        finally:
            db.close()

        seen = []

        async def fake_execute_query(*args, **kwargs):
            seen.append(llm_service._REQUEST_LLM_BASE_URL.get())
            return None

        with patch(
            "app.modules.chat_engine.router.QueryEngine.execute_query",
            new=fake_execute_query,
        ):
            resp = client.post(
                "/api/v1/chat/query",
                json={
                    "question": "ventas totales",
                    "connection_id": 1,
                    "llm_base_url": "http://127.0.0.1:11434",
                },
                headers=headers,
            )

        self.assertNotEqual(resp.status_code, 400, resp.text[:300])
        self.assertEqual(seen, ["http://127.0.0.1:11434"])


class TestPoliciesStaySeparated(unittest.TestCase):
    """El router endurece la URL del usuario; el sink NO se endurece."""

    def test_private_host_is_rejected_by_the_user_facing_policy(self):
        for url in PRIVATE_URLS:
            with self.subTest(url=url):
                with self.assertRaises(HTTPException) as ctx:
                    _resolve_llm_base_url(url)
                self.assertEqual(ctx.exception.status_code, 400)

    def test_sink_still_accepts_deployment_urls(self):
        """docker-compose: OLLAMA_BASE_URL=http://host.docker.internal:11434.
        Si esto se rompe, una instalacion en Docker queda sin LLM."""
        from app.core.config import settings

        for url in ("http://host.docker.internal:11434",
                    "http://192.168.1.50:11434",
                    settings.OLLAMA_BASE_URL):
            with self.subTest(url=url):
                self.assertTrue(validate_llm_base_url(url))

    def test_loopback_passes_both_policies(self):
        url = "http://127.0.0.1:11434"
        self.assertEqual(_resolve_llm_base_url(url), url)
        self.assertEqual(validate_llm_base_url(url), url)


def app_under_test():
    """Import perezoso de la app: `conftest` ya fijo la base de pruebas."""
    from main import app

    return app


if __name__ == "__main__":
    unittest.main()

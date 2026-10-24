"""
Politica LLM del chat: `LLM_MODE` decide si el usuario puede elegir motor.

- `central`/`external` (default de produccion): la URL y el modelo de Ajustes se
  IGNORAN. El backend usa el motor que administra la empresa y no emite ninguna
  request al host que mando el cliente.
- `user`: la empresa habilito el LLM propio por usuario. La URL se valida con
  `validate_llm_base_url` (loopback, `host.docker.internal` y RFC1918); link-local,
  hostnames internos e IPs publicas dan 400 antes de correr el motor.

Estos tests fijan las dos ramas en `/chat/query` y `/chat/query/stream`, y que el
sink siga aceptando la URL del despliegue.
"""
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal
from app.db.init_db import init_db
from app.modules.chat_engine import llm_service
from app.modules.chat_engine.llm_service import (
    LLMService,
    resolve_user_llm_config,
    validate_llm_base_url,
)
from app.modules.chat_engine.router import _request_llm_config
from app.modules.chat_engine.schemas import QueryRequest

from conftest import make_auth_headers

# En `user` se aceptan porque son parte de la red privada de la empresa.
USER_ALLOWED_URLS = [
    "http://127.0.0.1:11434",
    "http://localhost:11434",
    "http://192.168.1.50:11434",
    "http://10.0.0.5",
    "http://172.16.4.2:1234",
    "http://host.docker.internal:11434",
]

# Ni en `user`: link-local (metadatos cloud), hostnames internos que no sean
# `host.docker.internal`, e IPs publicas.
USER_REJECTED_URLS = [
    "http://169.254.169.254",
    "http://redis-interno:6379",
    "http://8.8.8.8:11434",
    "http://evil.example.com:11434",
]


def _no_request(*args, **kwargs):
    """Falla si el backend emite cualquier request HTTP."""
    raise AssertionError("Se emitio una request HTTP a un host no permitido")


def app_under_test():
    """Import perezoso de la app: `conftest` ya fijo la base de pruebas."""
    from main import app

    return app


class _ChatTestBase(unittest.TestCase):
    """Cliente autenticado y base de metadatos lista."""

    @classmethod
    def setUpClass(cls):
        db = SessionLocal()
        init_db(db)
        db.close()

    def setUp(self):
        # `raise_server_exceptions=False`: los fakes del motor devuelven None y la
        # request terminaria en 500. Lo que se verifica es que el motor corrio o no.
        self.client = TestClient(app_under_test(), raise_server_exceptions=False)
        self.db = SessionLocal()
        self.headers = make_auth_headers(self.db, "admin")

    def tearDown(self):
        self.db.close()

    def _payload(self, url, model=None):
        payload = {
            "question": "ventas totales",
            "connection_id": 1,
            "llm_base_url": url,
        }
        if model is not None:
            payload["llm_model"] = model
        return payload

    def _recording_execute(self, seen):
        async def fake_execute_query(*args, **kwargs):
            seen.append((
                llm_service._REQUEST_LLM_BASE_URL.get(),
                llm_service._REQUEST_LLM_MODEL.get(),
            ))
            return None

        return fake_execute_query


class TestCentralModeIgnoresUserConfig(_ChatTestBase):
    """Sin `LLM_MODE=user`, el cliente no elige el motor ni dispara requests."""

    def test_helper_ignores_url_and_model(self):
        with patch.object(settings, "LLM_MODE", "central"):
            self.assertEqual(
                resolve_user_llm_config("http://169.254.169.254", "mi-modelo"),
                (None, None),
            )

    def test_router_helper_ignores_hostile_config(self):
        with patch.object(settings, "LLM_MODE", "central"):
            out = _request_llm_config(QueryRequest(
                question="q",
                llm_base_url="http://192.168.1.50:11434",
                llm_model="mi-modelo",
            ))
        self.assertEqual(out, (None, None))

    def test_query_ignores_hostile_url_and_never_requests_it(self):
        seen = []
        with patch.object(settings, "LLM_MODE", "central"), patch(
            "app.modules.chat_engine.router.QueryEngine.execute_query",
            new=self._recording_execute(seen),
        ), patch.object(
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
                "/api/v1/chat/query",
                json=self._payload("http://169.254.169.254", "evil-model"),
                headers=self.headers,
            )

        self.assertNotEqual(resp.status_code, 400, resp.text[:300])
        # El motor corre, pero sin la config del cliente: usa la del servidor.
        self.assertEqual(seen, [(None, None)])
        mock_gen.assert_not_called()
        mock_stream.assert_not_called()

    def test_query_stream_ignores_hostile_url_and_never_requests_it(self):
        seen = []
        with patch.object(settings, "LLM_MODE", "central"), patch(
            "app.modules.chat_engine.router.QueryEngine.execute_query",
            new=self._recording_execute(seen),
        ), patch.object(
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
                "/api/v1/chat/query/stream",
                json=self._payload("http://169.254.169.254", "evil-model"),
                headers=self.headers,
            )
            # El generador se consume al leer el cuerpo; el fake corre dentro.
            _ = resp.text

        self.assertEqual(resp.status_code, 200, resp.text[:300])
        self.assertEqual(seen, [(None, None)])
        mock_gen.assert_not_called()
        mock_stream.assert_not_called()


class TestUserModeValidates(_ChatTestBase):
    """Con `LLM_MODE=user`, la URL del usuario se valida antes del motor."""

    def setUp(self):
        super().setUp()
        self._mode = patch.object(settings, "LLM_MODE", "user")
        self._mode.start()
        self.addCleanup(self._mode.stop)

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
                json=self._payload(url),
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

    def test_query_rejects_hosts_outside_the_policy(self):
        for url in USER_REJECTED_URLS:
            with self.subTest(url=url):
                self._assert_rejected("/api/v1/chat/query", url)

    def test_query_stream_rejects_hosts_outside_the_policy(self):
        for url in USER_REJECTED_URLS:
            with self.subTest(url=url):
                self._assert_rejected("/api/v1/chat/query/stream", url)

    def test_allowed_urls_reach_the_engine(self):
        for url in USER_ALLOWED_URLS:
            with self.subTest(url=url):
                seen = []
                with patch(
                    "app.modules.chat_engine.router.QueryEngine.execute_query",
                    new=self._recording_execute(seen),
                ), patch(
                    "httpx.AsyncClient.post", side_effect=_no_request
                ), patch(
                    "httpx.AsyncClient.get", side_effect=_no_request
                ), patch(
                    "httpx.AsyncClient.stream", side_effect=_no_request
                ):
                    resp = self.client.post(
                        "/api/v1/chat/query",
                        json=self._payload(url, "mi-modelo-2"),
                        headers=self.headers,
                    )

                self.assertNotEqual(resp.status_code, 400, resp.text[:300])
                self.assertEqual(seen, [(url, "mi-modelo-2")])

    def test_helper_returns_the_validated_url_and_model(self):
        base_url, model = _request_llm_config(QueryRequest(
            question="q",
            llm_base_url="http://127.0.0.1:11434/",
            llm_model="  mi-modelo-2  ",
        ))
        self.assertEqual(base_url, "http://127.0.0.1:11434")
        self.assertEqual(model, "mi-modelo-2")

    def test_helper_leaves_absent_config_as_none(self):
        self.assertEqual(
            _request_llm_config(QueryRequest(question="q")),
            (None, None),
        )

    def test_rejected_url_raises_400_without_http(self):
        with self.assertRaises(HTTPException) as ctx:
            resolve_user_llm_config("http://169.254.169.254", None)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_userinfo_trick_is_rejected(self):
        with self.assertRaises(HTTPException):
            resolve_user_llm_config("http://localhost@169.254.169.254", None)


class TestSinkPolicyStaysWiderThanTheOldRouterPolicy(unittest.TestCase):
    """El sink acepta la URL del despliegue (Docker/LAN), no solo loopback."""

    def test_sink_accepts_deployment_urls(self):
        for url in ("http://host.docker.internal:11434",
                    "http://192.168.1.50:11434",
                    settings.OLLAMA_BASE_URL):
            with self.subTest(url=url):
                self.assertTrue(validate_llm_base_url(url))

    def test_sink_rejects_cloud_metadata_and_internal_hostnames(self):
        for url in ("http://169.254.169.254",
                    "http://redis-interno:6379",
                    "http://8.8.8.8:11434"):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    validate_llm_base_url(url)

    def test_loopback_passes_both_policies(self):
        url = "http://127.0.0.1:11434"
        with patch.object(settings, "LLM_MODE", "user"):
            self.assertEqual(resolve_user_llm_config(url, None)[0], url)
        self.assertEqual(validate_llm_base_url(url), url)


if __name__ == "__main__":
    unittest.main()

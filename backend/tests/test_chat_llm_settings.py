"""
Item 5 (D1): la config LLM de Ajustes (URL/modelo) tiene que llegar al chat.

Hoy el backend solo acepta esos datos en `/llm/test-*` y `/system/health`:
`/chat/query` y `/chat/query/stream` los ignoran y el motor usa el servidor
default. Estos tests fijan las tres propiedades del fix:

1. Una URL fuera de la allowlist se rechaza con 400 y el motor NO corre
   (anti-SSRF, misma politica que `validate_llm_base_url` en el sink).
2. La config viaja POR REQUEST (ContextVar) hasta el motor, en el endpoint
   normal y dentro del task del stream.
3. `LLMService.generate_completion/stream_completion` la usan como default:
   el primer endpoint al que le pegan es el servidor de Ajustes.
"""
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from main import app
from app.core.database import SessionLocal
from app.db.init_db import init_db
from app.modules.auth.models import User, UserSession
from app.core.security import create_access_token
from app.modules.chat_engine.llm_service import LLMService


def _fake_http_client(captured):
    """Cliente httpx falso que registra (metodo, url) y nunca toca la red.

    `get`/`post` responden 500 (ningun candidato sirve, se recorre toda la
    cadena); `stream(...)` responde 200 con `aiter_lines()` vacio (ningun token).
    """

    class _Response:
        def __init__(self, status_code=500, text=""):
            self.status_code = status_code
            self.text = text

        def json(self):
            return {}

    class _StreamResponse:
        status_code = 200
        text = ""

        async def aiter_lines(self):
            for line in ():
                yield line

    class _StreamContext:
        def __init__(self, url):
            self.url = url

        async def __aenter__(self):
            captured.append(("STREAM", self.url))
            return _StreamResponse()

        async def __aexit__(self, *exc):
            return False

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kwargs):
            captured.append(("GET", url))
            return _Response()

        async def post(self, url, **kwargs):
            captured.append(("POST", url))
            return _Response()

        def stream(self, method, url, **kwargs):
            return _StreamContext(url)

    return _Client


class TestChatLLMSettingsRelay(unittest.TestCase):
    """La config de Ajustes llega al motor, o se rechaza antes de correrlo."""

    @classmethod
    def setUpClass(cls):
        db = SessionLocal()
        init_db(db)
        db.close()

    def setUp(self):
        # `raise_server_exceptions=False`: los fakes de abajo devuelven None y el
        # router termina en 500. Lo que se verifica es que el motor corrio con la
        # config de la request, no como termino la request.
        self.client = TestClient(app, raise_server_exceptions=False)
        self.db = SessionLocal()
        self.admin_user = self.db.query(User).filter(User.username == "admin").first()
        self.admin_jti = str(uuid.uuid4())
        self.admin_token = create_access_token(subject=self.admin_user.id, jti=self.admin_jti)
        self.db.add(UserSession(user_id=self.admin_user.id, jti=self.admin_jti, is_revoked=False))
        self.db.commit()
        self.headers = {"Authorization": f"Bearer {self.admin_token}"}

    def tearDown(self):
        self.db.close()

    def test_chat_query_rejects_non_allowed_llm_host(self):
        with patch(
            "app.modules.chat_engine.router.QueryEngine.execute_query",
            new_callable=AsyncMock,
        ) as mock_exec:
            resp = self.client.post(
                "/api/v1/chat/query",
                json={
                    "question": "ventas totales",
                    "connection_id": 1,
                    "llm_base_url": "http://169.254.169.254",
                },
                headers=self.headers,
            )

        self.assertEqual(resp.status_code, 400, resp.text[:300])
        mock_exec.assert_not_called()

    def _fake_execute_recording(self, seen):
        from app.modules.chat_engine import llm_service

        async def fake_execute_query(*args, **kwargs):
            seen.append((
                llm_service._REQUEST_LLM_BASE_URL.get(),
                llm_service._REQUEST_LLM_MODEL.get(),
            ))
            return None

        return fake_execute_query

    def _payload(self):
        return {
            "question": "ventas totales",
            "connection_id": 1,
            "llm_base_url": "http://127.0.0.1:11434",
            "llm_model": "mi-modelo-2",
        }

    def test_chat_query_sets_request_config_for_the_engine(self):
        seen = []
        with patch(
            "app.modules.chat_engine.router.QueryEngine.execute_query",
            new=self._fake_execute_recording(seen),
        ):
            self.client.post("/api/v1/chat/query", json=self._payload(), headers=self.headers)

        # Normalizada, sin barra final: lo que ve el motor ya paso por la
        # allowlist, no es el valor crudo del cliente.
        self.assertEqual(seen, [("http://127.0.0.1:11434", "mi-modelo-2")])

    def test_chat_query_stream_sets_request_config_inside_the_run_task(self):
        seen = []
        with patch(
            "app.modules.chat_engine.router.QueryEngine.execute_query",
            new=self._fake_execute_recording(seen),
        ):
            resp = self.client.post(
                "/api/v1/chat/query/stream", json=self._payload(), headers=self.headers
            )

        self.assertEqual(resp.status_code, 200, resp.text[:300])
        # El fake corre DENTRO del task del stream: si el `request_config`
        # hubiera quedado en el handler (ya retornado cuando el generador
        # corre), aca llegaria (None, None).
        self.assertEqual(seen, [("http://127.0.0.1:11434", "mi-modelo-2")])


class TestLLMServiceRequestConfigDefaults(unittest.IsolatedAsyncioTestCase):
    """Sin caller que pase base_url/model_name, gana la config de la request."""

    def setUp(self):
        # `_resolved_models` es cache de clase: limpiarla para no depender de
        # otra corrida en el mismo proceso.
        LLMService._resolved_models = {}

    async def test_generate_completion_uses_request_config_as_default(self):
        captured = []

        with patch("httpx.AsyncClient", new=_fake_http_client(captured)):
            with LLMService.request_config("http://127.0.0.1:9999", "modelo-z"):
                with self.assertRaises(Exception):
                    await LLMService.generate_completion("hola", max_tokens=1)

        posts = [url for method, url in captured if method == "POST"]
        self.assertTrue(posts, "no se emitio ninguna request")
        # Sin el fix, el primer POST seria http://127.0.0.1:8080/v1/chat/completions
        # (el default de llama.cpp), no el servidor de la request.
        self.assertEqual(posts[0], "http://127.0.0.1:9999/v1/chat/completions")

    async def test_stream_completion_uses_request_config_as_default(self):
        captured = []

        with patch("httpx.AsyncClient", new=_fake_http_client(captured)):
            with LLMService.request_config("http://127.0.0.1:9999", "modelo-z"):
                with self.assertRaises(Exception):
                    async for _ in LLMService.stream_completion("hola", max_tokens=1):
                        pass

        streams = [url for method, url in captured if method == "STREAM"]
        self.assertTrue(streams, "no se abrio ningun stream")
        self.assertEqual(streams[0], "http://127.0.0.1:9999/v1/chat/completions")


if __name__ == "__main__":
    unittest.main()

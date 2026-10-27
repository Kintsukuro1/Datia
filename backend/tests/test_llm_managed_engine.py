"""Motor LLM administrado y aislamiento de `LLM_MODE=user`.

Dos garantias:
1. `LLM_PROVIDER` decide la URL del motor que administra la empresa; antes
   `openai_compatible` estaba documentado y todos los caminos usaban Ollama.
2. Un `base_url` explicito (modo `user`) es el unico destino: si falla, la
   consulta falla. No se reintenta contra el motor central ni se devuelve el
   cuerpo del destino en el error.
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.core.config import managed_llm_base_url, settings
from app.modules.chat_engine.llm_service import LLMService
from app.modules.system.health_service import HealthService


class TestManagedEngineUrl(unittest.TestCase):
    def test_ollama_provider_uses_ollama_url(self):
        with patch.object(settings, "LLM_PROVIDER", "ollama"):
            self.assertEqual(managed_llm_base_url(), settings.OLLAMA_BASE_URL)

    def test_openai_compatible_provider_uses_its_own_url(self):
        with patch.object(settings, "LLM_PROVIDER", "openai_compatible"):
            self.assertEqual(managed_llm_base_url(), settings.OPENAI_COMPATIBLE_URL)

    def test_managed_chain_starts_with_the_configured_engine(self):
        with patch.object(settings, "LLM_PROVIDER", "openai_compatible"), \
             patch.object(settings, "OPENAI_COMPATIBLE_URL", "http://10.0.0.9:8000/v1"):
            candidates = LLMService._candidate_chat_urls(None)
        self.assertEqual(candidates[0][0], "http://10.0.0.9:8000")


class TestUserModeHasNoFallback(unittest.TestCase):
    def test_explicit_base_url_is_the_only_candidate(self):
        candidates = LLMService._candidate_chat_urls("http://10.0.0.9:8000/v1")
        self.assertEqual(
            candidates,
            [("http://10.0.0.9:8000", "http://10.0.0.9:8000/v1/chat/completions")],
        )

    def test_generate_completion_never_touches_the_managed_engine(self):
        seen = []

        async def fake_get(self, url, **kwargs):
            raise RuntimeError("connection refused")

        async def fake_post(self, url, **kwargs):
            seen.append(url)
            raise RuntimeError("connection refused")

        with patch.object(settings, "LLM_MODE", "user"), \
             patch("httpx.AsyncClient.get", new=fake_get), \
             patch("httpx.AsyncClient.post", new=fake_post):
            with self.assertRaises(Exception):
                asyncio.run(LLMService.generate_completion(
                    "hola", base_url="http://10.0.0.9:8000", max_tokens=1))

        self.assertTrue(seen, "no se intento ningun endpoint")
        self.assertTrue(
            all(u.startswith("http://10.0.0.9:8000") for u in seen),
            f"se filtro un endpoint ajeno al usuario: {seen}",
        )

    def test_error_does_not_echo_the_target_body(self):
        class _Res:
            status_code = 500
            text = '{"secreto": "elasticsearch"}'

        async def fake_get(self, url, **kwargs):
            raise RuntimeError("connection refused")

        async def fake_post(self, url, **kwargs):
            return _Res()

        with patch.object(settings, "LLM_MODE", "user"), \
             patch("httpx.AsyncClient.get", new=fake_get), \
             patch("httpx.AsyncClient.post", new=fake_post):
            with self.assertRaises(Exception) as ctx:
                asyncio.run(LLMService.generate_completion(
                    "hola", base_url="http://10.0.0.9:8000", max_tokens=1))

        self.assertNotIn("elasticsearch", str(ctx.exception))
        self.assertIn("500", str(ctx.exception))


class TestHealthProbesTheConfiguredEngineOnly(unittest.TestCase):
    """En produccion, un Ollama suelto en loopback no maquilla un motor caido."""

    async def _get_ok(self, url, **kwargs):
        class _Res:
            status_code = 200

            @staticmethod
            def json():
                return {"models": [{"name": "m"}]}

        return _Res()

    @staticmethod
    async def _only_loopback_ollama(url, timeout):
        return url == "http://localhost:11434"

    def test_production_does_not_fall_back_to_loopback(self):
        with patch.object(settings, "ENVIRONMENT", "production"), \
             patch.object(settings, "LLM_PROVIDER", "ollama"), \
             patch("app.modules.system.health_service._port_open",
                   new=self._only_loopback_ollama), \
             patch("httpx.AsyncClient.get", new=self._get_ok):
            res = asyncio.run(HealthService.check_llm_connectivity(
                provider="ollama", base_url="http://ollama:11434",
                model_name="m", timeout=0.1, trusted=True))

        self.assertFalse(res["success"], res)

    def test_development_still_discovers_a_local_engine(self):
        with patch.object(settings, "ENVIRONMENT", "development"), \
             patch.object(settings, "LLM_PROVIDER", "ollama"), \
             patch("app.modules.system.health_service._port_open",
                   new=self._only_loopback_ollama), \
             patch("httpx.AsyncClient.get", new=self._get_ok):
            res = asyncio.run(HealthService.check_llm_connectivity(
                provider="ollama", base_url="http://ollama:11434",
                model_name="m", timeout=0.1, trusted=True))

        self.assertTrue(res["success"], res)
        self.assertIn("11434", res["active_url"])

    def test_failure_does_not_invent_the_configured_model(self):
        """Si nadie respondio, la lista de modelos va vacia.

        Antes se devolvia `[OLLAMA_MODEL]` y el Studio la pintaba como "Modelos
        Reportados por el Servidor" debajo del error de conexion: el diagnostico
        afirmaba que el motor tenia un modelo que nunca reporto.
        """
        with patch.object(settings, "ENVIRONMENT", "production"), \
             patch("app.modules.system.health_service._port_open",
                   new=AsyncMock(return_value=False)):
            res = asyncio.run(HealthService.check_llm_connectivity(
                provider="ollama", base_url="http://127.0.0.1:11434",
                model_name="qwen2.5-coder:7b", timeout=0.1, trusted=True))

        self.assertFalse(res["success"], res)
        self.assertEqual(res["available_models"], [])


if __name__ == "__main__":
    unittest.main()

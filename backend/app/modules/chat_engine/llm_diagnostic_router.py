import time
import httpx
from typing import Any, List, Optional
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.modules.auth.models import User
from app.modules.system.health_service import HealthService
from app.core.config import settings, managed_llm_base_url
from app.core.prompts import PromptManager
from app.modules.chat_engine.llm_service import LLMService, resolve_user_llm_config

# Timeout de la prueba de inferencia. Antes eran 15 s hardcodeados en cada uno de
# los tres `AsyncClient`, y eso reportaba FALLO en un LLM local de CPU que antes
# si respondia: una inferencia de 27B cuantizado se pasa de 15 s sin estar
# colgada. El motor real ya tiene su timeout declarado (`LLMService.LLM_TIMEOUT`),
# asi que se reusa en vez de mantener un segundo numero que nadie actualiza.
#
# `timeout=3.0` en `test-connection` NO se toca: ahi lo que se mide es si el
# puerto esta vivo, no si el modelo piensa, y esperar 300 s para descubrir que no
# hay nadie escuchando es lo que hace inservible el boton "Probar".
_INFERENCE_TIMEOUT = LLMService.LLM_TIMEOUT

llm_diagnostic_router = APIRouter()

# Subconjunto de loopback que usa el SINK (`validate_llm_base_url`). La
# politica de `LLM_MODE=user` es mas amplia (RFC1918 y host.docker.internal,
# ver `llm_service.validate_llm_base_url`); esta allowlist ya no decide que
# hosts puede elegir un usuario, solo cierra el caso loopback en el sink.
LLM_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _resolve_llm_base_url(raw: str) -> str:
    """
    Valida el `base_url` que manda el cliente y devuelve la base usable.

    Levanta 400 sin hacer NINGUNA request si el host no esta en el allowlist, de
    modo que una URL invalida no llega a tocar la red.
    """
    url = (raw or "").strip()
    if not url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="base_url es obligatorio.",
        )
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"base_url invalida: '{raw}'. Debe ser una URL http/https.",
        )
    if parts.hostname.lower() not in LLM_ALLOWED_HOSTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Host '{parts.hostname}' no permitido. El servidor LLM debe correr "
                f"en local ({', '.join(sorted(LLM_ALLOWED_HOSTS))})."
            ),
        )
    return url.rstrip('/')

class LLMTestRequest(BaseModel):
    provider: str = "ollama"  # "ollama" | "openai_compatible" | "llama_cpp" | "custom"
    base_url: Optional[str] = None  # e.g. "http://127.0.0.1:8080" or "http://localhost:11434"
    model_name: Optional[str] = None

class LLMTestResponse(BaseModel):
    success: bool
    message: str
    available_models: List[str] = []
    latency_ms: int = 0
    active_url: str = ""

class LLMCompletionTestRequest(BaseModel):
    prompt: str
    provider: str = "ollama"
    base_url: Optional[str] = None
    model_name: Optional[str] = None

class LLMCompletionTestResponse(BaseModel):
    success: bool
    completion_text: str
    latency_ms: int = 0
    message: str
    active_url: str = ""


@llm_diagnostic_router.post("/test-connection", response_model=LLMTestResponse)
async def test_llm_connection(
    req: LLMTestRequest,
    current_user: User = Depends(get_current_user)
) -> Any:
    """Tests live HTTP connectivity to the LLM selected by the company policy."""
    # En central/external la URL y el modelo del cliente se ignoran; en user se
    # validan antes de tocar la red. Levanta 400 sin emitir ninguna request.
    base_url, model_name = resolve_user_llm_config(req.base_url, req.model_name)
    effective_base = base_url or managed_llm_base_url()
    effective_model = model_name or settings.OLLAMA_MODEL
    if settings.LLM_MODE_EFFECTIVE != "user":
        effective_provider = settings.LLM_PROVIDER
    else:
        effective_provider = req.provider or settings.LLM_PROVIDER
    result = await HealthService.check_llm_connectivity(
        provider=effective_provider,
        base_url=effective_base,
        model_name=effective_model,
        timeout=3.0,
        trusted=True,
    )
    return LLMTestResponse(
        success=result["success"],
        message=result["message"],
        # Lista vacia cuando el servidor no reporta modelos. Earlier esto devolvia
        # `[req.model_name]` como fallback: el admin guardaba un modelo que el
        # servidor nunca dijo tener, y la consulta fallaba despues. Un endpoint
        # que responde `/health` no conoce sus modelos, y announcear uno seria
        # inventarlo. El frontend ya lo tenia corregido; el bypass permitia que
        # esta mentira volviera.
        available_models=result.get("available_models", []),
        latency_ms=result.get("latency_ms", 0),
        active_url=result.get("active_url", ""),
    )

@llm_diagnostic_router.post("/test-completion", response_model=LLMCompletionTestResponse)
async def test_llm_completion(
    req: LLMCompletionTestRequest,
    current_user: User = Depends(get_current_user)
) -> Any:
    """Executes real inference completion against Ollama or llama.cpp / OpenAI-compatible endpoint."""
    start_time = time.time()
    base_url, model_name = resolve_user_llm_config(req.base_url, req.model_name)
    effective_base = base_url or managed_llm_base_url()
    effective_model = model_name or settings.OLLAMA_MODEL
    # `_as_base_url` quita el `/v1` final: sin eso, un endpoint configurado como
    # `https://llm.empresa/v1` recibia `/v1/v1/chat/completions`.
    url = LLMService._as_base_url(effective_base)
    # No existe PromptManager.SQL_TEST_SYSTEM_PROMPT: esa linea era un
    # AttributeError garantizado en cada request, ejecutado ANTES de los tres
    # try y sin exception_handler en la app, o sea un 500 siempre. El endpoint
    # solo prueba conectividad, asi que alcanza con el prompt base.
    system_prompt = PromptManager.DEFAULT_SYSTEM_PROMPT

    chat_url = f"{url}/v1/chat/completions"
    payload_chat = {
        "model": effective_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": req.prompt}
        ],
        "temperature": 0.1
    }

    try:
        async with httpx.AsyncClient(timeout=_INFERENCE_TIMEOUT) as client:
            res = await client.post(chat_url, json=payload_chat)
            latency = int((time.time() - start_time) * 1000)
            if res.status_code == 200:
                choices = res.json().get("choices", [])
                completion = choices[0].get("message", {}).get("content", "").strip() if choices else ""
                return LLMCompletionTestResponse(
                    success=True,
                    completion_text=completion,
                    latency_ms=latency,
                    message=f"Inferencia completada por llama.cpp / servidor LLM local ({effective_model}).",
                    active_url=url,
                )
    except Exception:
        pass

    native_url = f"{url}/completion"
    payload_native = {
        "prompt": f"System: {system_prompt}\nUser: {req.prompt}\nAssistant:",
        "temperature": 0.1,
        "n_predict": 256
    }
    try:
        async with httpx.AsyncClient(timeout=_INFERENCE_TIMEOUT) as client:
            res = await client.post(native_url, json=payload_native)
            latency = int((time.time() - start_time) * 1000)
            if res.status_code == 200:
                completion = res.json().get("content", "").strip()
                return LLMCompletionTestResponse(
                    success=True,
                    completion_text=completion,
                    latency_ms=latency,
                    message="Inferencia completada por llama.cpp (Endpoint Nativo /completion).",
                    active_url=url,
                )
    except Exception:
        pass

    ollama_url = f"{url}/api/generate"
    payload_ollama = {
        "model": effective_model,
        "prompt": req.prompt,
        "system": system_prompt,
        "stream": False,
        "options": {"temperature": 0.1}
    }
    try:
        async with httpx.AsyncClient(timeout=_INFERENCE_TIMEOUT) as client:
            res = await client.post(ollama_url, json=payload_ollama)
            latency = int((time.time() - start_time) * 1000)
            if res.status_code == 200:
                completion = res.json().get("response", "").strip()
                return LLMCompletionTestResponse(
                    success=True,
                    completion_text=completion,
                    latency_ms=latency,
                    message=f"Inferencia completada por Ollama ({effective_model}).",
                    active_url=url,
                )
    except Exception:
        pass

    return LLMCompletionTestResponse(
        success=False,
        completion_text="",
        latency_ms=int((time.time() - start_time) * 1000),
        message=f"No se pudo completar la inferencia en {url}. Verifique que el servidor LLM configurado esté activo.",
        active_url=url,
    )

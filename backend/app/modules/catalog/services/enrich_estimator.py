"""
Duracion estimada del enriquecimiento con IA, MEDIDA antes de iniciarlo.

Por que mide y no le pregunta al modelo
---------------------------------------
Un LLM no sabe a que velocidad corre su hardware: pedirle el tiempo que va a
tardar devuelve una alucinacion con formato de numero. Lo que si se puede medir
es tokens/segundo con un probe corto, y el trabajo real (tablas y columnas) sale
de la misma introspeccion que alimenta al enricher. La estimacion resultante es
un RANGO: la formula no puede predecir si el modelo devuelve menos tokens que su
techo permite, ni cuanto tarda el prompt de cada tabla.

El probe pasa por `generate_completion` (la cadena completa de endpoints con su
resolucion de modelo) para NO duplicar esa logica. Su latencia incluye el
procesamiento del prompt, asi que subestima la velocidad de generacion: el error
va en la direccion segura (duracion estimada mas larga, no mas corta).
"""
import time
from typing import Any, Dict, List, Optional, Tuple

from app.modules.admin_catalog.models import CorporateConnection
from app.modules.chat_engine.llm_service import LLMService
from app.modules.catalog.services.catalog_enricher import (
    LLM_COLUMNS_PER_CALL,
    LLM_MAX_TOKENS_CAP,
    LLM_TOKENS_PER_COLUMN,
    LLM_TOTAL_BUDGET_S,
)
from app.modules.catalog.services.schema_inspector import SchemaInspector

# --- Constantes de calibracion ------------------------------------------------
# Techo de salida por tabla del enricher: min(cap, 90*columnas + 128). Se estima
# que el modelo cumple el ~60% de ese techo (los JSON tipicos son mas cortos).
_EXPECTED_OUTPUT_RATIO = 0.6
# Costo del prompt: ~8 tokens por columna (nombre+tipo) mas el encabezado.
_PROMPT_TOKENS_PER_TABLE = 20
_PROMPT_TOKENS_PER_COLUMN = 8
# En CPU el procesamiento del prompt suele ser varias veces mas rapido que la
# generacion; 4x es una referencia conservadora para no prometer de menos.
_PROMPT_SPEED_FACTOR = 4.0
# Costo fijo por LLAMADA (conexion + handshake del servidor local).
_TABLE_OVERHEAD_S = 0.5
# Banda de incertidumbre: la estimacion se muestra como rango, no como numero.
_RANGE_LOW = 0.7
_RANGE_HIGH = 1.6

_PROBE_PROMPT = "Escribe los numeros del 1 al 30 separados por comas, sin texto adicional."
_PROBE_MAX_TOKENS = 48

# La velocidad del hardware no cambia entre wizards: no se paga el probe dos
# veces para la misma combinacion (base_url, modelo).
_SPEED_CACHE: Dict[Tuple[str, str], Tuple[float, float]] = {}
_SPEED_CACHE_TTL_S = 3600.0


class EnrichEstimator:
    """Mide y calcula cuanto tardaria el enriquecimiento con IA de una conexion."""

    @classmethod
    async def measure_tokens_per_second(
        cls,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> Optional[float]:
        """Probe de generacion corto -> tokens/segundo. None si no hay servidor.

        `chars/4` es una aproximacion gruesa de tokens para espanol; con un probe
        de salida fija el error relativo se mantiene chico y el rango visible
        absorbe el resto.
        """
        key = (base_url or "", model_name or "")
        now = time.time()
        cached = _SPEED_CACHE.get(key)
        if cached and now - cached[1] < _SPEED_CACHE_TTL_S:
            return cached[0]

        start = time.time()
        try:
            with LLMService.request_config(base_url, model_name):
                text = await LLMService.generate_completion(
                    _PROBE_PROMPT,
                    max_tokens=_PROBE_MAX_TOKENS,
                    temperature=0.1,
                )
        except Exception:
            return None
        elapsed = max(time.time() - start, 0.001)
        if not text:
            return None

        tps = (len(text) / 4.0) / elapsed
        if tps <= 0:
            return None
        _SPEED_CACHE[key] = (tps, now)
        return tps

    @classmethod
    def estimate_seconds_from_counts(
        cls,
        column_counts: List[int],
        tokens_per_second: float,
    ) -> Dict[str, Any]:
        """Matematica pura: cuantas tablas/columnas -> cuanto tiempo, a una velocidad dada."""
        if tokens_per_second <= 0:
            raise ValueError("tokens_per_second debe ser > 0")

        # El enricher llama por LOTE de columnas, no por tabla entera: la
        # estimacion modela los mismos lotes, o subestima el trabajo de las
        # tablas anchas (mas prompts y mas overhead por llamada).
        chunk_size = max(1, LLM_COLUMNS_PER_CALL)
        output_tokens = 0.0
        prompt_tokens = 0
        calls = 0
        for count in column_counts:
            remaining = count
            while remaining > 0:
                chunk = min(chunk_size, remaining)
                cap = min(LLM_MAX_TOKENS_CAP, LLM_TOKENS_PER_COLUMN * chunk + 128)
                output_tokens += cap * _EXPECTED_OUTPUT_RATIO
                prompt_tokens += _PROMPT_TOKENS_PER_TABLE + _PROMPT_TOKENS_PER_COLUMN * chunk
                calls += 1
                remaining -= chunk

        generation_s = output_tokens / tokens_per_second
        prompt_s = prompt_tokens / (tokens_per_second * _PROMPT_SPEED_FACTOR)
        overhead_s = calls * _TABLE_OVERHEAD_S
        total = generation_s + prompt_s + overhead_s

        return {
            "estimated_seconds": round(total),
            "min_seconds": max(round(total * _RANGE_LOW), 5),
            "max_seconds": max(round(total * _RANGE_HIGH), 10),
            "beyond_budget": total > LLM_TOTAL_BUDGET_S,
        }

    @classmethod
    async def estimate_for_connection(
        cls,
        conn: CorporateConnection,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Estima para UNA conexion. Propaga `SchemaIntrospectionError` (el router la mapea a 502)."""
        tables_meta = SchemaInspector.introspect_connection_metadata(conn)

        total_tables = len(tables_meta or [])
        total_columns = sum(len(t.get("columns") or []) for t in (tables_meta or []))

        if not tables_meta:
            return {
                "available": False,
                "message": "No se obtuvo metadata de ninguna tabla (conexion inaccesible o vacia).",
                "total_tables": 0,
                "total_columns": 0,
                "budget_seconds": int(LLM_TOTAL_BUDGET_S),
            }

        tps = await cls.measure_tokens_per_second(base_url, model_name)
        if tps is None:
            return {
                "available": False,
                "message": (
                    "El modelo de IA local no respondio al sondeo de velocidad; "
                    "no se puede estimar la duracion."
                ),
                "total_tables": total_tables,
                "total_columns": total_columns,
                "budget_seconds": int(LLM_TOTAL_BUDGET_S),
            }

        estimate = cls.estimate_seconds_from_counts(
            [len(t.get("columns") or []) for t in tables_meta], tps
        )
        return {
            "available": True,
            "message": "",
            "total_tables": total_tables,
            "total_columns": total_columns,
            "tokens_per_second": round(tps, 1),
            **estimate,
            "budget_seconds": int(LLM_TOTAL_BUDGET_S),
        }

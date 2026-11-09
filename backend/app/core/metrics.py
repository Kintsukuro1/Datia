"""Métricas mínimas en formato Prometheus, sin dependencias externas.

No se etiqueta por path para no explotar la cardinalidad: método + status
alcanza para alertar de una caída o de una tasa de 5xx. Las métricas de LLM se
incrementan desde `LLMService` cuando el semáforo empieza/termina una llamada.
"""
import threading
from collections import defaultdict

_lock = threading.Lock()
_http_requests = defaultdict(int)
_http_duration_count = 0
_http_duration_sum = 0.0
_llm_requests = 0
_llm_errors = 0


def observe_http_request(method: str, status: int, duration_s: float) -> None:
    global _http_duration_count, _http_duration_sum
    with _lock:
        _http_requests[(method or "UNKNOWN", str(status))] += 1
        _http_duration_count += 1
        _http_duration_sum += max(0.0, duration_s)


def inc_llm_request() -> None:
    global _llm_requests
    with _lock:
        _llm_requests += 1


def inc_llm_error() -> None:
    global _llm_errors
    with _lock:
        _llm_errors += 1


def render_metrics() -> str:
    with _lock:
        requests = dict(_http_requests)
        duration_count = _http_duration_count
        duration_sum = _http_duration_sum
        llm_requests = _llm_requests
        llm_errors = _llm_errors

    lines = [
        "# HELP datia_http_requests_total Requests HTTP por método y status.",
        "# TYPE datia_http_requests_total counter",
    ]
    for (method, status), count in sorted(requests.items()):
        lines.append(
            f'datia_http_requests_total{{method="{method}",status="{status}"}} {count}'
        )
    lines.extend([
        "# HELP datia_http_request_duration_seconds Duración acumulada de requests.",
        "# TYPE datia_http_request_duration_seconds summary",
        f"datia_http_request_duration_seconds_count {duration_count}",
        f"datia_http_request_duration_seconds_sum {duration_sum:.6f}",
        "# HELP datia_llm_requests_total Llamadas al motor LLM.",
        "# TYPE datia_llm_requests_total counter",
        f"datia_llm_requests_total {llm_requests}",
        "# HELP datia_llm_errors_total Llamadas al motor LLM que terminaron en error.",
        "# TYPE datia_llm_errors_total counter",
        f"datia_llm_errors_total {llm_errors}",
    ])
    return "\n".join(lines) + "\n"


def reset_metrics_for_tests() -> None:
    """Solo tests: deja el registro en cero para aserciones deterministas."""
    global _http_duration_count, _http_duration_sum, _llm_requests, _llm_errors
    with _lock:
        _http_requests.clear()
        _http_duration_count = 0
        _http_duration_sum = 0.0
        _llm_requests = 0
        _llm_errors = 0

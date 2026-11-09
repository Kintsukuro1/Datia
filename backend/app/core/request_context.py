"""Contexto por request para logs y respuestas de error.

El `request_id` se genera en el middleware ASGI y viaja por `ContextVar` para
que cualquier log del mismo request lo incluya sin pasarlo por parámetro.
"""
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    return request_id_var.get()

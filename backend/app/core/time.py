"""Reloj unico de la aplicacion.

Las columnas `DateTime` de la metadata son `TIMESTAMP WITHOUT TIME ZONE` y
guardan UTC naive. `datetime.utcnow()` devolvia exactamente eso, pero esta
deprecado desde Python 3.12. Reemplazarlo por `datetime.now(timezone.utc)` a
secas devolveria un datetime CON timezone: al escribirlo en una columna naive
PostgreSQL puede reinterpretarlo segun la zona de la sesion y correr la hora.
Este helper conserva el contrato anterior: UTC, sin tzinfo.
"""

import datetime


def utcnow_naive() -> datetime.datetime:
    """Devuelve la hora actual en UTC sin `tzinfo`, para columnas naive."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

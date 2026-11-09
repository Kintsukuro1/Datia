"""La auditoria de `/projections/run` no es opcional: si no se puede escribir, no
hay respuesta.

Que se verifica aca
-------------------
H3a. El camino exitoso persiste la proyeccion con `required=True`. Antes, un
     fallo de escritura devolvia los numeros con `audit_log_id=None`: datos del
     cliente publicados sin trazabilidad. El test hace fallar la escritura en la
     capa mas baja (`commit`) y exige que el router levante `AuditWriteError` en
     vez de devolver la respuesta.

H3b. Una excepcion inesperada (conector caido, bug de calculo) deja fila con
     `validation_status="ERROR_EJECUCION"` y `required=True`, y la excepcion
     original sigue subiendo. Antes el 500 no dejaba rastro en `audit_logs`.

H2 (el borde que faltaba). El LLM elige `id_cliente` como metrica y el analisis
     cae a la heuristica; el selector manual, en cambio, la rechaza con un
     motivo. Son dos puertas distintas del mismo predicado
     (`_is_num_col and looks_like_metric`): un ID numerico no es una metrica.

Por que se llama a la corrutina y no se pasa por `TestClient`
-------------------------------------------------------------
El contrato que importa es "la funcion del endpoint no retorna". Pasar por HTTP
agrega el mapeo a 500 de Starlette, que no es lo que se cambio en Fase 1 y
ademas exige overrides globales de `get_db`/`get_current_user`. Los gates de
datos se reemplazan por un stub porque aca no se prueba RBAC (eso es
`test_ml_service_rbac.py`): lo que se prueba es que el resultado de la cadena se
audite o no se publique.

Ninguno de estos tests necesita Postgres ni el servidor de inferencia: los
bloques sincronicos de `ml_service` se reemplazan y la sesion que abre
`_in_thread` es perezosa (no se conecta si no se consulta).
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.api.deps import AuditWriteError
from app.modules.chat_engine.forecast_service import (
    ForecastUnavailable,
    resolve_columns,
)
from app.modules.ml_forecast import ml_service
from app.modules.ml_forecast import router as ml_router
from app.modules.ml_forecast.schemas import ProjectionRequest

COLS = [
    {"name": "fecha", "type": "date", "is_pk": False},
    {"name": "valor", "type": "numeric", "is_pk": False},
]

# Lo minimo que devuelve la cadena real (`load_fact_context` -> `analyze_question`
# -> `run_projection`), sin tocar la base.
CONTEXT = {
    "table": "movimientos",
    "cols": COLS,
    "candidate_cols": COLS,
    "heuristic": {"date_col": "fecha", "value_col": "valor", "entity_col": None},
}
ANALYSIS = {
    "table": "movimientos",
    "metric_column": "valor",
    "date_column": "fecha",
    "horizon": 6,
    "horizon_source": "pedido",
    "source": "heuristica",
}
DATA = {
    "available": True,
    "horizon": 6,
    "series": [{"periodo": "2026-01", "valor": 100.0}],
    "steps": [{
        "step": 1, "period": "2026-02", "point": 110.0, "lower": 90.0,
        "upper": 130.0, "band_pct": 20.0, "mape": None, "n_backtests": 0,
        "reliable": False, "extrapolated": False,
    }],
    "total": {
        "point": 110.0, "lower": 90.0, "upper": 130.0, "band_pct": 20.0,
        "mape": None, "n_backtests": 0, "reliable": False,
        "extrapolated": True, "measured_steps": 0,
    },
    "method": "naive",
    "beats_naive": False,
    "mape": None,
    "mape_by_method": {},
    "n_periods": 1,
    "has_gaps": False,
    "income_only": True,
    "sql": 'SELECT 1 AS periodo, 100 AS valor FROM "movimientos"',
    "table": "movimientos",
    "metric_column": "valor",
    "date_column": "fecha",
}

_LLM = "app.modules.chat_engine.llm_service.LLMService.generate_completion"


class _DbConCommitRoto:
    """Sesion cuyo `commit` falla: el escenario real de disco/DB caida al auditar.

    Se rompe la capa mas baja y no `_persist_audit_log` para que el test ejercite
    de verdad el `required=True` de produccion: con `required=False` el helper
    traga la excepcion y devuelve None, y la proyeccion se publicaria sin
    trazabilidad (el test fallaria, que es lo que tiene que pasar).
    """

    def add(self, _obj):
        pass

    def commit(self):
        raise RuntimeError("no se pudo escribir en audit_logs")

    def refresh(self, _obj):
        pass

    def rollback(self):
        pass


class TestAuditoriaDeProyeccion(unittest.TestCase):
    def setUp(self):
        self.user = SimpleNamespace(id=7, username="tester", is_admin=False, role_id=2)
        self.payload = ProjectionRequest(
            question="proyecta los ingresos mensuales",
            connection_id=3,
            horizon=6,
        )

    def _contexto(self, *, load=None):
        return (
            # La resolución de conexión no es el objeto de este test; se
            # reemplaza para no exigir una sesión real con metadata.
            patch.object(
                ml_router, "_resolve_projection_connection",
                return_value=SimpleNamespace(id=3, name="movimientos"),
            ),
            patch.object(
                ml_router, "_projection_gates",
                return_value=("movimientos", "analista", {}),
            ),
            patch.object(
                ml_service, "load_fact_context",
                new=load or MagicMock(return_value=dict(CONTEXT)),
            ),
            patch.object(
                ml_service, "analyze_question",
                new=AsyncMock(return_value=dict(ANALYSIS)),
            ),
            patch.object(
                ml_service, "run_projection",
                new=MagicMock(return_value=dict(DATA)),
            ),
            patch.object(
                ml_service, "narrate",
                new=AsyncMock(return_value=("La serie sube.", "plantilla")),
            ),
        )

    def _correr(self, db, *, load=None):
        resolve, gates, load_patch, analyze, run, narrate = self._contexto(load=load)
        with resolve, gates, load_patch, analyze, run, narrate:
            return asyncio.run(
                ml_router.run_ml_projection(self.payload, self.user, db)
            )

    # --- H3a ---------------------------------------------------------------

    def test_h3a_fallo_de_auditoria_en_el_exito_no_devuelve_la_proyeccion(self):
        """Con numeros calculados, no poder auditarlos cancela la respuesta."""
        with self.assertRaises(AuditWriteError):
            self._correr(_DbConCommitRoto())

    # --- H3b ---------------------------------------------------------------

    def test_h3b_excepcion_inesperada_audita_ERROR_EJECUCION_y_se_propaga(self):
        """El 500 tambien deja rastro: estado propio, y obligatorio."""
        audit = MagicMock(return_value=99)
        caido = MagicMock(side_effect=RuntimeError("conector caido"))

        with patch.object(
            ml_router, "_resolve_projection_connection",
            return_value=SimpleNamespace(id=3, name="movimientos"),
        ), patch.object(
            ml_router, "_projection_gates",
            return_value=("movimientos", "analista", {}),
        ), patch.object(ml_service, "load_fact_context", new=caido), patch.object(
            ml_router, "_persist_audit_log", new=audit,
        ):
            with self.assertRaises(RuntimeError):
                asyncio.run(
                    ml_router.run_ml_projection(self.payload, self.user, _DbConCommitRoto())
                )

        audit.assert_called_once()
        kwargs = audit.call_args.kwargs
        self.assertEqual(kwargs["validation_status"], "ERROR_EJECUCION")
        self.assertIs(kwargs["required"], True)
        self.assertIsNone(kwargs["sql_generated"])
        self.assertIn("RuntimeError", kwargs["error_message"])

    # --- H2 (lo que no cubria `_validate_llm_choice`) -----------------------

    def test_h2_el_llm_no_puede_elegir_un_id_y_cae_a_la_heuristica(self):
        """`id_cliente` es numerico pero no es una metrica: se descarta ENTERA."""
        cols = [
            {"name": "fecha", "type": "date", "is_pk": False},
            {"name": "id_cliente", "type": "bigint", "is_pk": False},
            {"name": "monto", "type": "numeric", "is_pk": False},
        ]
        respuesta = '{"metric_column": "id_cliente", "date_column": "fecha", "horizon": 6}'

        with patch(_LLM, new=AsyncMock(return_value=respuesta)):
            out = asyncio.run(ml_service.analyze_question(
                "proyecta los ingresos", "movimientos", cols,
                resolve_columns(cols), 6,
            ))

        self.assertEqual(out["source"], "heuristica")
        self.assertEqual(out["metric_column"], "monto")
        self.assertEqual(out["date_column"], "fecha")

    def test_h2_el_selector_manual_rechaza_un_id_con_motivo(self):
        """Un pedido manual invalido no se reemplaza en silencio: se explica."""
        cols = [
            {"name": "fecha", "type": "date", "is_pk": False},
            {"name": "id_cliente", "type": "bigint", "is_pk": False},
            {"name": "monto", "type": "numeric", "is_pk": False},
        ]

        with self.assertRaises(ForecastUnavailable) as ctx:
            asyncio.run(ml_service.analyze_question(
                "proyecta los ingresos", "movimientos", cols,
                resolve_columns(cols), 6,
                requested_metric="id_cliente", requested_date="fecha",
            ))

        self.assertIn("no es una metrica numerica permitida", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

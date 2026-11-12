"""Proyecciones con historia corta y escenarios por granularidad.

Estos tests son deliberadamente puros: no montan la base ni el LLM. Cubren el
contrato que hace que una serie corta no sea un dead end y que una tasa anual
no se confunda con una tasa semanal o trimestral.
"""

from __future__ import annotations

import unittest

from app.modules.ml_forecast.ml_service import (
    _growth_scenarios,
    _guidance_for_series,
    _scenario_comparison,
    _scenario_projection,
)
from app.modules.ml_forecast.policy import POLICY
from app.modules.ml_forecast.schemas import ProjectionRequest, ProjectionResponse


def _months(count: int) -> list[str]:
    return [f"2024-{index % 12 + 1:02d}" for index in range(count)]


def _quarters(count: int) -> list[str]:
    return [
        f"{2024 + index // 4}-Q{index % 4 + 1}"
        for index in range(count)
    ]


def _weeks(count: int) -> list[str]:
    return [f"2025-W{index + 1:02d}" for index in range(count)]


class TestProjectionResilience(unittest.TestCase):
    def test_escenario_explicito_admite_horizonte_largo(self):
        req = ProjectionRequest(
            question="proyecta ventas",
            horizon=120,
            scenario_growth_pct=10.0,
        )
        self.assertEqual(req.horizon, 120)
        self.assertEqual(req.scenario_growth_pct, 10.0)

    def test_horizonte_largo_sin_escenario_sigue_rechazado(self):
        with self.assertRaises(ValueError):
            ProjectionRequest(question="proyecta ventas", horizon=120)

    def test_escenario_fuera_de_rango_se_rechaza(self):
        for value in (-100.0, 1001.0):
            with self.assertRaises(ValueError):
                ProjectionRequest(
                    question="proyecta ventas",
                    horizon=6,
                    scenario_growth_pct=value,
                )

    def test_serie_corta_recibe_guia_y_no_un_silencio(self):
        for n in range(1, POLICY.min_periods):
            text = _guidance_for_series(n)
            self.assertIsNotNone(text, n)
            self.assertIn(str(n), text)
            self.assertIn("escenario", text.lower())

        self.assertIsNone(_guidance_for_series(POLICY.min_periods))

    def test_proyeccion_por_escenario_no_inventa_banda(self):
        values = [100.0, 110.0, 120.0, 130.0]
        out = _scenario_projection(values, _quarters(4), 4, "quarter", 10.0)

        self.assertTrue(out["available"], out)
        self.assertEqual(out["method"], "scenario")
        self.assertIsNone(out["total"]["lower"])
        self.assertIsNone(out["total"]["upper"])
        self.assertIsNone(out["total"]["band_pct"])
        self.assertEqual(len(out["steps"]), 4)
        # La base es el ultimo valor y la tasa es anual: 10% en 4 trimestres
        # termina en 130 * 1.10, no en 130 * 1.025.
        self.assertAlmostEqual(out["scenario"]["final_value"], 143.0, places=2)

    def test_comparacion_con_serie_corta_usa_ultimo_valor_como_base(self):
        values = [100.0, 110.0, 120.0, 130.0]
        comparison = _scenario_comparison(
            values, _quarters(4), 4, "quarter", 10.0
        )

        self.assertIsNone(comparison["base_total"])
        self.assertAlmostEqual(comparison["base_final"], 130.0)
        self.assertGreaterEqual(len(comparison["scenarios"]), 2)
        self.assertTrue(all(item["label"] for item in comparison["scenarios"]))

    def test_trimestral_anualiza_la_tasa_cuando_no_hay_un_ano(self):
        # 2,5% por trimestre compuesto = 10,38% anual. Se pide 20% para que el
        # supuesto no se confunda con la tasa historica por la tolerancia.
        values = [100.0, 102.5, 105.0625, 107.6890625]
        scenarios = _growth_scenarios(values, _quarters(4), 20.0, "quarter")

        rates = [rate for rate, _ in scenarios]
        self.assertTrue(
            any(abs(rate - 10.381) < 0.02 for rate in rates),
            rates,
        )
        self.assertIn((20.0, "Tu supuesto"), scenarios)

    def test_semanal_anualiza_la_tasa_cuando_no_hay_un_ano(self):
        # 1% semanal compuesto = 67,77% anual.
        values = [100.0 * (1.01 ** index) for index in range(8)]
        scenarios = _growth_scenarios(values, _weeks(8), 10.0, "week")

        rates = [rate for rate, _ in scenarios]
        self.assertTrue(any(abs(rate - 67.77) < 0.05 for rate in rates), rates)

    def test_mensual_con_un_ano_usa_el_mismo_mes_del_ano_anterior(self):
        values = [100.0 * (1.10 ** (index / 12.0)) for index in range(25)]
        scenarios = _growth_scenarios(values, _months(25), 10.0, "month")

        rates = [rate for rate, _ in scenarios]
        self.assertTrue(any(abs(rate - 10.0) < 0.01 for rate in rates), rates)

    def test_politica_publica_los_limites_que_la_ui_necesita(self):
        public = POLICY.public()
        self.assertEqual(public["max_horizon"], POLICY.max_horizon)
        self.assertEqual(public["max_scenario_horizon"], POLICY.max_scenario_horizon)
        self.assertEqual(public["min_periods"], POLICY.min_periods)
        self.assertIn("reliable_backtests", public)

    def test_respuesta_vieja_sigue_aceptando_el_contrato_nuevo(self):
        response = ProjectionResponse(
            available=False,
            reason="serie corta",
            question="proyecta ventas",
            horizon=6,
        )
        self.assertIsNone(response.policy)
        self.assertEqual(response.metrics_by_method, {})
        self.assertEqual(response.model_comparison, [])
        self.assertFalse(response.can_forecast)


if __name__ == "__main__":
    unittest.main()

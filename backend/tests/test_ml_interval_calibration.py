"""Calibración de intervalos: cuantil, cobertura y fallback por poca evidencia."""

from __future__ import annotations

import math
import unittest

from app.modules.ml_forecast.ml_models import (
    _calibrated_interval,
    build_projection,
)
from app.modules.ml_forecast.policy import POLICY


def _serie_larga(n: int = 40) -> list[float]:
    return [100.0 + 10.0 * math.sin(index / 2.0) + index * 0.5 for index in range(n)]


class TestCalibracionDeIntervalos(unittest.TestCase):
    def test_cuantil_con_correccion_finita(self):
        signed = [float(value) for value in range(-10, 10)]

        interval = _calibrated_interval(signed)

        self.assertEqual(interval["band_method"], "conformal_quantile")
        # n=20, objetivo 80%: rangos 2 y 19 sobre n+1.
        self.assertEqual(interval["lower_pct"], -9.0)
        self.assertEqual(interval["upper_pct"], 8.0)
        # El peor caso se publica aparte: la banda calibrada es más angosta.
        self.assertEqual(interval["worst_case_pct"], 10.0)
        self.assertEqual(interval["band_pct"], 9.0)

    def test_pocos_residuos_caen_al_peor_caso(self):
        signed = [-5.0, 2.0, 4.0, 6.0, 8.0, -1.0, 3.0]

        interval = _calibrated_interval(signed)

        self.assertEqual(interval["band_method"], "max_residual")
        self.assertEqual(interval["lower_pct"], -8.0)
        self.assertEqual(interval["upper_pct"], 8.0)
        self.assertEqual(interval["worst_case_pct"], 8.0)

    def test_cambio_de_nivel_fuerza_peor_caso(self):
        out = build_projection(_serie_larga(), 6, has_level_shift=True)

        for step in out["steps"]:
            if step["n_backtests"]:
                self.assertEqual(step["band_method"], "max_residual", step)
                self.assertFalse(step["reliable"], step)

    def test_banda_calibrada_es_mas_angosta_que_el_peor_caso(self):
        out = build_projection(_serie_larga(), 6)

        calibrados = [
            step for step in out["steps"]
            if step["band_method"] == "conformal_quantile"
        ]
        self.assertTrue(calibrados, out["steps"])
        self.assertTrue(
            any(
                step["band_pct"] < step["worst_case_pct"]
                for step in calibrados
            ),
            calibrados,
        )
        for step in calibrados:
            self.assertLessEqual(step["band_pct"], step["worst_case_pct"], step)
            self.assertGreaterEqual(step["interval_coverage_n"], POLICY.interval_min_residuals)

    def test_intervalo_contiene_el_punto_y_conserva_el_sesgo(self):
        # Todos los residuos positivos: el modelo subestima. El limite inferior
        # se colapsa al punto, el superior conserva el sesgo.
        interval = _calibrated_interval([5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0])

        self.assertLessEqual(interval["lower_pct"], 0.0)
        self.assertGreater(interval["upper_pct"], 0.0)
        self.assertGreater(abs(interval["upper_pct"]), abs(interval["lower_pct"]))

    def test_cobertura_walk_forward_es_menor_que_los_residuos_totales(self):
        out = build_projection(_serie_larga(), 3)
        step = out["steps"][0]

        self.assertIsNotNone(step["interval_coverage"], step)
        self.assertGreaterEqual(step["interval_coverage"], 0.0)
        self.assertLessEqual(step["interval_coverage"], 1.0)
        # La cobertura mira desde `interval_min_residuals`; no usa todos los
        # backtests del paso.
        self.assertLess(step["interval_coverage_n"], step["n_backtests"])

    def test_pasos_sin_backtest_heredan_el_intervalo_y_lo_declaran(self):
        out = build_projection(_serie_larga(20), 30)
        ultimo_medido = [step for step in out["steps"] if not step["extrapolated"]][-1]
        extrapolados = [step for step in out["steps"] if step["extrapolated"]]

        self.assertTrue(extrapolados)
        for step in extrapolados:
            self.assertEqual(step["band_method"], "carried", step)
            self.assertIsNone(step["interval_coverage"], step)
            self.assertEqual(step["band_pct"], ultimo_medido["band_pct"], step)

    def test_objetivo_de_cobertura_viaja_en_el_payload(self):
        out = build_projection(_serie_larga(), 3)

        self.assertEqual(out["interval_target"], POLICY.interval_coverage)
        self.assertEqual(out["total"]["interval_target"], POLICY.interval_coverage)


if __name__ == "__main__":
    unittest.main()

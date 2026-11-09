"""Calidad por reglas: estados y razones, sin base de datos ni UI."""

import unittest

from app.modules.ml_forecast import ml_service
from app.modules.ml_forecast.ml_models import (
    EXPLORATORY_MAPE,
    UNRELIABLE_MAPE,
    evaluate_quality,
)


def _projection(
    *, mape=10.0, beats_naive=True, measured=6, backtests=5, has_steps=True
):
    total = None if measured is None else {"measured_steps": measured}
    steps = (
        [{"point": 1.0, "n_backtests": backtests}]
        if has_steps else []
    )
    return {
        "mape": mape,
        "beats_naive": beats_naive,
        "total": total,
        "steps": steps,
    }


class TestCalidadPorReglas(unittest.TestCase):
    def _quality(self, projection, *, horizon=6, has_gaps=False):
        return evaluate_quality(projection, horizon=horizon, has_gaps=has_gaps)

    def test_sin_medicion_si_no_hay_mape(self):
        out = self._quality(_projection(mape=None))
        self.assertEqual(out["status"], "sin_medicion")
        self.assertIsNone(out["mape"])
        self.assertIn("no hay MAPE medible", out["reasons"])

    def test_no_confiable_por_mape(self):
        out = self._quality(_projection(mape=67.7))
        self.assertEqual(out["status"], "no_confiable")
        self.assertIn("MAPE 67,7% supera el 50%", out["reasons"])
        self.assertEqual(out["unreliable_threshold"], UNRELIABLE_MAPE)
        self.assertEqual(out["exploratory_threshold"], EXPLORATORY_MAPE)

    def test_no_confiable_si_no_le_gana_al_naive(self):
        out = self._quality(_projection(mape=10, beats_naive=False))
        self.assertEqual(out["status"], "no_confiable")
        self.assertIn("no le gana al naive", out["reasons"])

    def test_no_confiable_si_no_hay_total(self):
        out = self._quality(_projection(mape=10, measured=None))
        self.assertEqual(out["status"], "no_confiable")
        reasons = " ".join(out["reasons"])
        self.assertIn("no se pudo calcular el total", reasons)
        self.assertNotIn("no hay pasos medidos", reasons)

    def test_no_confiable_si_el_total_no_tiene_pasos_medidos(self):
        out = self._quality(_projection(mape=10, measured=None))
        # El `total=None` es el caso anterior; este cubre total presente con
        # `measured_steps=None`.
        out = self._quality({
            "mape": 10,
            "beats_naive": True,
            "total": {"measured_steps": None},
            "steps": [{"point": 1.0, "n_backtests": 5}],
        })
        self.assertEqual(out["status"], "no_confiable")
        self.assertIn("no hay pasos medidos", " ".join(out["reasons"]))

    def test_no_confiable_si_la_banda_no_cubre_el_horizonte(self):
        out = self._quality(_projection(mape=10, measured=3))
        self.assertEqual(out["status"], "no_confiable")
        self.assertIn("cubre 3 de 6 pasos", " ".join(out["reasons"]))

    def test_exploratoria_por_mape_entre_30_y_50(self):
        out = self._quality(_projection(mape=40))
        self.assertEqual(out["status"], "exploratoria")
        self.assertIn("MAPE 40% supera el 30%", out["reasons"])

    def test_exploratoria_por_pocos_backtests(self):
        out = self._quality(_projection(mape=10, backtests=2))
        self.assertEqual(out["status"], "exploratoria")
        self.assertIn("2 backtests", " ".join(out["reasons"]))

    def test_exploratoria_sin_comparacion_contra_naive(self):
        out = self._quality(_projection(mape=10, beats_naive=None))
        self.assertEqual(out["status"], "exploratoria")
        self.assertIn("sin comparación contra naive", out["reasons"])

    def test_exploratoria_con_huecos(self):
        out = self._quality(_projection(mape=10), has_gaps=True)
        self.assertEqual(out["status"], "exploratoria")
        self.assertIn("periodos faltantes", " ".join(out["reasons"]))

    def test_confiable_con_toda_la_evidencia(self):
        out = self._quality(_projection(mape=20))
        self.assertEqual(out["status"], "confiable")
        self.assertEqual(out["reasons"], [])

    def test_el_estado_ganador_no_mezcla_razones_del_peor(self):
        out = self._quality(_projection(mape=67.7, backtests=1, measured=3))
        self.assertEqual(out["status"], "no_confiable")
        self.assertIn("MAPE", " ".join(out["reasons"]))
        self.assertIn("cubre 3 de 6", " ".join(out["reasons"]))
        self.assertNotIn("backtests", " ".join(out["reasons"]))


class TestBandaDeConteo(unittest.TestCase):
    def _projection(self):
        return {
            "steps": [
                {"lower": -5.0, "upper": 10.0},
                {"lower": 2.0, "upper": 8.0},
            ],
            "total": {"lower": -1.0, "upper": 20.0},
        }

    def test_count_acota_la_banda_inferior_a_cero(self):
        projection = self._projection()
        ml_service._clamp_count_bands(projection, "count")

        self.assertEqual(projection["steps"][0]["lower"], 0.0)
        self.assertTrue(projection["steps"][0]["lower_clamped"])
        self.assertEqual(projection["steps"][0]["upper"], 10.0)
        self.assertEqual(projection["steps"][1]["lower"], 2.0)
        self.assertNotIn("lower_clamped", projection["steps"][1])
        self.assertEqual(projection["total"]["lower"], 0.0)
        self.assertTrue(projection["total"]["lower_clamped"])

    def test_count_distinct_tambien_acota(self):
        projection = self._projection()
        ml_service._clamp_count_bands(projection, "count_distinct")
        self.assertEqual(projection["steps"][0]["lower"], 0.0)

    def test_una_suma_no_se_acota(self):
        projection = self._projection()
        ml_service._clamp_count_bands(projection, "sum")
        self.assertEqual(projection["steps"][0]["lower"], -5.0)
        self.assertNotIn("lower_clamped", projection["steps"][0])


if __name__ == "__main__":
    unittest.main()

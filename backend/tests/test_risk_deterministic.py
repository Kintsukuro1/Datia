"""El riesgo de concentración lo calcula el backend, no el LLM."""
import unittest

from app.modules.chat_engine.kpi_calculator import KPICalculator


class TestDeterministicRisk(unittest.TestCase):

    def _risk(self, values):
        rows = [{"monto": v} for v in values]
        return KPICalculator.deterministic_risk(rows, ["monto"])

    def test_alta_concentracion_es_alto(self):
        self.assertEqual(self._risk([75, 10, 10, 5]), "ALTO")

    def test_concentracion_media_es_medio(self):
        self.assertEqual(self._risk([45.8, 30, 24.2]), "MEDIO")

    def test_distribucion_pareja_es_bajo(self):
        self.assertEqual(self._risk([10, 10, 10]), "BAJO")

    def test_sin_metrica_medible_es_none(self):
        self.assertIsNone(self._risk([100, -10, -10]))

    def test_pocas_filas_es_none(self):
        self.assertIsNone(self._risk([100, 0]))

    def test_sin_filas_es_none(self):
        self.assertIsNone(KPICalculator.deterministic_risk([], []))


if __name__ == "__main__":
    unittest.main()

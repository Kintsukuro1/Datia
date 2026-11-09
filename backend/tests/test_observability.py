"""Request ID, `/version` y `/metrics` sin dependencias externas."""
import unittest

from fastapi.testclient import TestClient

from app.core.metrics import reset_metrics_for_tests
from main import app


class TestObservability(unittest.TestCase):

    def setUp(self):
        reset_metrics_for_tests()
        self.client = TestClient(app)

    def test_request_id_se_propaga_y_vuelve_en_la_respuesta(self):
        res = self.client.get("/health", headers={"X-Request-ID": "soporte-123"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("X-Request-ID"), "soporte-123")

    def test_metrics_expone_el_conteo_de_requests(self):
        self.client.get("/health")
        res = self.client.get("/metrics")
        self.assertEqual(res.status_code, 200)
        self.assertIn("datia_http_requests_total", res.text)
        self.assertIn('status="200"', res.text)
        self.assertIn("datia_llm_requests_total", res.text)

    def test_version_no_inventa_un_build(self):
        res = self.client.get("/version")
        self.assertEqual(res.status_code, 200)
        self.assertIn("version", res.json())
        self.assertIn("commit", res.json())


if __name__ == "__main__":
    unittest.main()

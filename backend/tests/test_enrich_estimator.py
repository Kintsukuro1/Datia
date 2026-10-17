"""
Tests de EnrichEstimator: la estimacion de duracion del enriquecimiento con IA.

Contrato:
  1. La matematica responde a tablas/columnas y a tokens/segundo medidos.
  2. El probe mide, cachea por (base_url, modelo), y devuelve None sin LLM.
  3. Sin metadata o sin probe: `available=False` con totales reales, sin inventar tiempo.
"""
import asyncio
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from conftest import pg_source_conn_attrs

from main import app
from app.core.database import SessionLocal
from app.core.security import create_access_token
from app.db.init_db import init_db
from app.modules.admin_catalog.models import CorporateConnection
from app.modules.auth.models import User, UserSession
from app.modules.catalog.services import enrich_estimator
from app.modules.catalog.services.enrich_estimator import EnrichEstimator
from app.modules.catalog.services.schema_inspector import SchemaInspector
from app.modules.chat_engine.llm_service import LLMService


def _table_meta(schema, table, n_columns):
    return {
        "schema_name": schema,
        "table_name": table,
        "row_count": 1,
        "columns": [
            {"name": f"col_{i}", "data_type": "TEXT", "is_pk": False,
             "is_nullable": True, "default_value": None, "sample_values": []}
            for i in range(n_columns)
        ],
    }


class TestEstimateMath(unittest.TestCase):

    def test_more_columns_means_more_time(self):
        small = EnrichEstimator.estimate_seconds_from_counts([2], 10.0)
        big = EnrichEstimator.estimate_seconds_from_counts([20], 10.0)
        self.assertLess(small["estimated_seconds"], big["estimated_seconds"])

    def test_higher_throughput_means_less_time(self):
        slow = EnrichEstimator.estimate_seconds_from_counts([10], 5.0)
        fast = EnrichEstimator.estimate_seconds_from_counts([10], 50.0)
        self.assertLess(fast["estimated_seconds"], slow["estimated_seconds"])

    def test_range_brackets_the_estimate(self):
        est = EnrichEstimator.estimate_seconds_from_counts([5, 8, 3], 12.0)
        self.assertLessEqual(est["min_seconds"], est["estimated_seconds"])
        self.assertLessEqual(est["estimated_seconds"], est["max_seconds"])

    def test_beyond_budget_flag_fires_for_big_jobs(self):
        # 40 tablas x 50 columnas a 3 tok/s: muy por encima del presupuesto.
        est = EnrichEstimator.estimate_seconds_from_counts([50] * 40, 3.0)
        self.assertTrue(est["beyond_budget"])

    def test_zero_tps_raises(self):
        with self.assertRaises(ValueError):
            EnrichEstimator.estimate_seconds_from_counts([1], 0.0)


class TestMeasureTokensPerSecond(unittest.TestCase):

    def setUp(self):
        enrich_estimator._SPEED_CACHE.clear()

    def test_measures_and_caches(self):
        completion = AsyncMock(return_value="1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12")
        with patch.object(LLMService, "generate_completion", new=completion):
            tps1 = asyncio.run(
                EnrichEstimator.measure_tokens_per_second("http://127.0.0.1:8080", "modelo")
            )
            tps2 = asyncio.run(
                EnrichEstimator.measure_tokens_per_second("http://127.0.0.1:8080", "modelo")
            )

        self.assertIsNotNone(tps1)
        self.assertGreater(tps1, 0)
        self.assertEqual(tps1, tps2)
        self.assertEqual(completion.await_count, 1, "el segundo llamado no debía re-probar")

    def test_returns_none_without_server(self):
        with patch.object(
            LLMService, "generate_completion",
            new=AsyncMock(side_effect=RuntimeError("refused")),
        ):
            tps = asyncio.run(EnrichEstimator.measure_tokens_per_second())

        self.assertIsNone(tps)
        self.assertEqual(len(enrich_estimator._SPEED_CACHE), 0, "un fallo no se cachea")


class TestEstimateForConnection(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        db = SessionLocal()
        init_db(db)
        db.close()

    def setUp(self):
        self.db = SessionLocal()

        # La introspeccion va parcheada en todos los tests de estimacion: la
        # conexion solo tiene que existir y ser valida, no apuntar a una base
        # viva.
        self.conn = CorporateConnection(
            name=f"estimate_{uuid.uuid4().hex[:8]}",
            **pg_source_conn_attrs("no_conecta"),
            is_active=True,
        )
        self.db.add(self.conn)
        self.db.commit()

    def tearDown(self):
        self.db.delete(self.conn)
        self.db.commit()
        self.db.close()

    def test_estimates_with_measured_speed(self):
        meta = [_table_meta("public", "ventas", 2), _table_meta("public", "clientes", 3)]
        with patch.object(
            SchemaInspector, "introspect_connection_metadata", return_value=meta
        ), patch.object(
            EnrichEstimator, "measure_tokens_per_second",
            new=AsyncMock(return_value=20.0),
        ):
            res = asyncio.run(EnrichEstimator.estimate_for_connection(self.conn))

        self.assertTrue(res["available"])
        self.assertEqual(res["total_tables"], 2)
        self.assertEqual(res["total_columns"], 5)
        self.assertEqual(res["tokens_per_second"], 20.0)
        self.assertGreater(res["estimated_seconds"], 0)
        self.assertIn("beyond_budget", res)

    def test_probe_failure_reports_unavailable_with_real_counts(self):
        meta = [_table_meta("public", "ventas", 2)]
        with patch.object(
            SchemaInspector, "introspect_connection_metadata", return_value=meta
        ), patch.object(
            EnrichEstimator, "measure_tokens_per_second",
            new=AsyncMock(return_value=None),
        ):
            res = asyncio.run(EnrichEstimator.estimate_for_connection(self.conn))

        self.assertFalse(res["available"])
        self.assertEqual(res["total_tables"], 1)
        self.assertEqual(res["total_columns"], 2)
        self.assertIsNone(res.get("estimated_seconds"))
        self.assertIn("sondeo de velocidad", res["message"])

    def test_empty_metadata_reports_unavailable(self):
        with patch.object(
            SchemaInspector, "introspect_connection_metadata", return_value=[]
        ):
            res = asyncio.run(EnrichEstimator.estimate_for_connection(self.conn))

        self.assertFalse(res["available"])
        self.assertIn("No se obtuvo metadata", res["message"])


class TestEnrichEstimateEndpoint(unittest.TestCase):
    """Capa HTTP real del endpoint: auth admin, validación SSRF, 404 y contrato."""

    @classmethod
    def setUpClass(cls):
        db = SessionLocal()
        init_db(db)
        db.close()

    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()

        admin = self.db.query(User).filter(User.username == "admin").first()
        jti = str(uuid.uuid4())
        token = create_access_token(subject=admin.id, jti=jti)
        self.db.add(UserSession(user_id=admin.id, jti=jti, is_revoked=False))
        self.db.commit()
        self.headers = {"Authorization": f"Bearer {token}"}

        self.conn = CorporateConnection(
            name=f"est_ep_{uuid.uuid4().hex[:8]}",
            **pg_source_conn_attrs("no_conecta"),
            is_active=True,
        )
        self.db.add(self.conn)
        self.db.commit()

    def tearDown(self):
        self.db.delete(self.conn)
        self.db.commit()
        self.db.close()

    def test_404_for_unknown_connection(self):
        res = self.client.get(
            "/api/v1/catalog/connections/999999/enrich-estimate", headers=self.headers
        )
        self.assertEqual(res.status_code, 404)

    def test_400_for_non_loopback_base_url(self):
        # El allowlist anti-SSRF corta antes de emitir una sola request.
        res = self.client.get(
            f"/api/v1/catalog/connections/{self.conn.id}/enrich-estimate",
            params={"base_url": "http://evil.example.com:11434"},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 400, res.text)

    def test_200_reports_measured_estimate(self):
        meta = [_table_meta("public", "ventas", 2)]
        with patch.object(
            SchemaInspector, "introspect_connection_metadata", return_value=meta
        ), patch.object(
            EnrichEstimator, "measure_tokens_per_second",
            new=AsyncMock(return_value=15.0),
        ):
            res = self.client.get(
                f"/api/v1/catalog/connections/{self.conn.id}/enrich-estimate",
                headers=self.headers,
            )
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertTrue(data["available"])
        self.assertEqual(data["total_tables"], 1)
        self.assertEqual(data["total_columns"], 2)
        self.assertEqual(data["tokens_per_second"], 15.0)
        self.assertGreater(data["estimated_seconds"], 0)


if __name__ == "__main__":
    unittest.main()

"""
Tests de la fase real de IA de `auto_enrich_catalog` (`use_llm=True`).

Cubre el contrato que reemplazo a la fase LLM sin cota del commit c4cb6bc:
  1. El modelo reemplaza placeholders heuristicos y se cuenta en `llm_enriched_count`.
  2. Respuesta inusable / error / timeout -> el catalogo queda con heuristicas,
     sin propagar excepcion y sin declarar IA donde no hubo.
  3. Texto curado a mano NO se pisa, aunque `is_ai_generated` diga True.
  4. LLM caido al chequeo de conectividad -> no se llama al modelo ni una vez.
"""
import asyncio
import json
import time
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from conftest import (
    create_temp_pg_database,
    drop_temp_pg_database,
    pg_source_conn_attrs,
    pg_source_engine,
)

from main import app
from app.core.database import SessionLocal
from app.core.security import create_access_token
from app.db.init_db import init_db
from app.modules.admin_catalog.models import CorporateConnection, DatabaseType, SemanticCatalog
from app.modules.admin_catalog.schemas import AutoEnrichRequest
from app.modules.auth.models import User, UserSession
from app.modules.catalog.services import enrich_estimator
from app.modules.catalog.services.catalog_enricher import CatalogEnricher
from app.modules.chat_engine.llm_service import LLMService
from app.modules.system.health_service import HealthService


def _run_enrich(db, *args, **kwargs):
    return asyncio.run(CatalogEnricher.auto_enrich_catalog(db, *args, **kwargs))


def _llm_payload():
    return json.dumps([
        {
            "column": "monto",
            "friendly_name": "Importe Neto de Venta",
            "description": "Monto neto cobrado por la venta registrada.",
            "business_formula": "SUM(monto) * 0.81",
        },
        {
            "column": "fecha",
            "friendly_name": "Fecha de Venta",
            "description": "Dia calendario en que se registro la operacion.",
            "business_formula": "DATE(fecha)",
        },
    ])


def _payload_for_prompt(prompt: str) -> str:
    """JSON valido con una entrada por columna listada en el prompt.

    El mock responde a lo que PIDIO el lote, no a una lista fija: con lotes,
    cada llamada lleva un subconjunto distinto de columnas.
    """
    segment = prompt.split("Columnas (nombre | tipo): ", 1)[1].split(".\n", 1)[0]
    names = [part.split(" | ", 1)[0].strip() for part in segment.split("; ")]
    return json.dumps([
        {
            "column": name,
            "friendly_name": f"Nombre de negocio {name}",
            "description": f"Descripcion de negocio para {name}.",
            "business_formula": f"SUM({name})",
        }
        for name in names
    ])


class TestLlmEnrichmentPhase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        db = SessionLocal()
        init_db(db)
        db.close()

        # Fuente desechable: base PostgreSQL real con la tabla que se enriquece.
        cls.pg_db = create_temp_pg_database("enrichllm")
        eng = pg_source_engine(cls.pg_db)
        try:
            from sqlalchemy import text
            with eng.begin() as c:
                c.execute(text("CREATE TABLE ventas (monto INTEGER, fecha TEXT)"))
        finally:
            eng.dispose()

    @classmethod
    def tearDownClass(cls):
        drop_temp_pg_database(cls.pg_db)

    def setUp(self):
        self.db = SessionLocal()

        # Velocidad medida pre-cargada: sin esto el probe de la estimacion
        # (mockeado junto a la generacion) correria por test y el tamaño de
        # lote quedaria a merced del mock. 10 tok/s -> lote 6 (el techo).
        enrich_estimator._SPEED_CACHE[("", "")] = (10.0, time.time())

        self.conn = CorporateConnection(
            name=f"llm_enrich_{uuid.uuid4().hex[:8]}",
            **pg_source_conn_attrs(self.pg_db),
            is_active=True,
        )
        self.db.add(self.conn)
        self.db.commit()

    def tearDown(self):
        self.db.query(SemanticCatalog).filter(
            SemanticCatalog.connection_id == self.conn.id
        ).delete(synchronize_session=False)
        self.db.delete(self.conn)
        self.db.commit()
        self.db.close()

    def _seed_heuristics(self):
        """Primera corrida sin IA: deja los placeholders heuristicos."""
        return _run_enrich(self.db, AutoEnrichRequest(connection_id=self.conn.id))

    def _run_with_llm(self, completion_mock, health_success=True, timeout_s=None):
        patches = [
            patch.object(
                HealthService, "check_llm_connectivity",
                new_callable=AsyncMock,
                return_value={"success": health_success, "message": "probe"},
            ),
            patch.object(LLMService, "generate_completion", new=completion_mock),
        ]
        if timeout_s is not None:
            patches.append(
                patch("app.modules.catalog.services.catalog_enricher.LLM_TABLE_TIMEOUT_S", timeout_s)
            )
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return _run_enrich(
            self.db, AutoEnrichRequest(connection_id=self.conn.id, use_llm=True)
        )

    def _row(self, column):
        return self.db.query(SemanticCatalog).filter(
            SemanticCatalog.connection_id == self.conn.id,
            SemanticCatalog.table_name == "ventas",
            SemanticCatalog.column_name == column,
        ).first()

    # ------------------------------------------------------------------ feliz

    def test_llm_replaces_heuristic_placeholders(self):
        self._seed_heuristics()

        resp = self._run_with_llm(AsyncMock(return_value=_llm_payload()))

        self.assertTrue(resp.success, resp.message)
        self.assertEqual(resp.llm_enriched_count, 2)
        self.assertIn("La IA describió 2 campo(s)", resp.message)

        monto = self._row("monto")
        self.assertEqual(monto.description, "Monto neto cobrado por la venta registrada.")
        self.assertEqual(monto.friendly_name, "Importe Neto de Venta")
        self.assertTrue(monto.is_ai_generated)
        fecha = self._row("fecha")
        self.assertEqual(fecha.business_formula, "DATE(fecha)")

    # ------------------------------------------------------------- degradado

    def test_unusable_llm_output_keeps_heuristics(self):
        self._seed_heuristics()
        before = self._row("fecha").description

        resp = self._run_with_llm(AsyncMock(return_value=None))

        self.assertTrue(resp.success, resp.message)
        self.assertEqual(resp.llm_enriched_count, 0)
        self.assertIn("no devolvió descripciones usables", resp.message)
        self.assertEqual(self._row("fecha").description, before)

    def test_llm_error_does_not_propagate(self):
        self._seed_heuristics()
        before = self._row("monto").description

        resp = self._run_with_llm(AsyncMock(side_effect=RuntimeError("connection refused")))

        self.assertTrue(resp.success, resp.message)
        self.assertEqual(resp.llm_enriched_count, 0)
        self.assertEqual(self._row("monto").description, before)

    def test_table_timeout_is_bounded(self):
        self._seed_heuristics()

        async def _slow(*args, **kwargs):
            await asyncio.sleep(5)
            return _llm_payload()

        start = time.time()
        resp = self._run_with_llm(AsyncMock(side_effect=_slow), timeout_s=0.05)
        elapsed = time.time() - start

        self.assertLess(elapsed, 2.0, "el timeout por tabla no acoto la llamada")
        self.assertEqual(resp.llm_enriched_count, 0)
        self.assertTrue(resp.success, resp.message)

    def test_llm_down_at_probe_skips_model_entirely(self):
        self._seed_heuristics()
        completion = AsyncMock(return_value=_llm_payload())

        resp = self._run_with_llm(completion, health_success=False)

        self.assertEqual(completion.await_count, 0, "se llamó al modelo con el probe caído")
        self.assertEqual(resp.llm_enriched_count, 0)
        self.assertIn("no respondió al chequeo de conectividad", resp.message)

    # ------------------------------------------------------------- curado

    def test_curated_text_survives_llm_even_with_flag_true(self):
        """El flag `is_ai_generated=True` no es permiso para pisar texto curado."""
        curated = SemanticCatalog(
            connection_id=self.conn.id,
            schema_name="public",
            table_name="ventas",
            column_name="monto",
            friendly_name="Monto Neto Curado",
            description="Descripcion escrita a mano por el administrador.",
            business_formula="SUM(monto) - descuentos",
            is_ai_generated=True,
        )
        self.db.add(curated)
        self.db.commit()
        self._seed_heuristics()  # siembra `fecha`; no toca `monto` (no vacío)

        resp = self._run_with_llm(AsyncMock(return_value=_llm_payload()))

        monto = self._row("monto")
        self.assertEqual(monto.description, "Descripcion escrita a mano por el administrador.")
        self.assertEqual(monto.friendly_name, "Monto Neto Curado")
        self.assertEqual(monto.business_formula, "SUM(monto) - descuentos")
        self.assertEqual(resp.llm_enriched_count, 1, "solo `fecha` era elegible")


class TestAutoEnrichHttpContract(unittest.TestCase):
    """Capa HTTP: el body con `use_llm` parsea y la respuesta declara el conteo IA."""

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

    def tearDown(self):
        self.db.close()

    @patch.object(
        HealthService, "check_llm_connectivity",
        new_callable=AsyncMock,
        return_value={"success": False, "message": "sin LLM"},
    )
    def test_use_llm_with_dead_llm_reports_zero(self, _health):
        res = self.client.post(
            "/api/v1/catalog/auto-enrich", json={"use_llm": True}, headers=self.headers
        )
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertIn("llm_enriched_count", data)
        self.assertEqual(data["llm_enriched_count"], 0)
        self.assertIn("llm_stopped_early", data)
        self.assertFalse(data["llm_stopped_early"])
        self.assertIn("no respondió al chequeo de conectividad", data["message"])


class TestLlmChunkedEnrichment(unittest.TestCase):
    """Tablas anchas: el enricher llama por LOTES, no por tabla entera.

    Con UNA llamada por tabla, un JSON de 13+ columnas se trunca por
    `max_tokens` o se pasa del techo por llamada, y la tabla pierde TODA la IA
    por esa unica llamada (caso real del wizard con un 7B Q4 en CPU).
    """

    WIDE_COLUMNS = 13

    @classmethod
    def setUpClass(cls):
        db = SessionLocal()
        init_db(db)
        db.close()

        # Fuente desechable propia: la tabla ancha no debe cambiar las
        # expectativas de los tests de la clase anterior.
        cls.pg_db = create_temp_pg_database("enrichchunk")
        eng = pg_source_engine(cls.pg_db)
        try:
            from sqlalchemy import text
            cols = ", ".join(f"c{i} INTEGER" for i in range(1, cls.WIDE_COLUMNS + 1))
            with eng.begin() as c:
                c.execute(text(f"CREATE TABLE movimientos ({cols})"))
        finally:
            eng.dispose()

    @classmethod
    def tearDownClass(cls):
        drop_temp_pg_database(cls.pg_db)

    def setUp(self):
        self.db = SessionLocal()
        # Velocidad medida pre-cargada: lote fijo en 6 y sin probe colandose
        # en el mock de generacion.
        enrich_estimator._SPEED_CACHE[("", "")] = (10.0, time.time())

        self.conn = CorporateConnection(
            name=f"llm_chunk_{uuid.uuid4().hex[:8]}",
            **pg_source_conn_attrs(self.pg_db),
            is_active=True,
        )
        self.db.add(self.conn)
        self.db.commit()
        _run_enrich(self.db, AutoEnrichRequest(connection_id=self.conn.id))

    def tearDown(self):
        self.db.query(SemanticCatalog).filter(
            SemanticCatalog.connection_id == self.conn.id
        ).delete(synchronize_session=False)
        self.db.delete(self.conn)
        self.db.commit()
        self.db.close()

    def _run_with_llm(self, completion_mock):
        patches = [
            patch.object(
                HealthService, "check_llm_connectivity",
                new_callable=AsyncMock,
                return_value={"success": True, "message": "probe"},
            ),
            patch.object(LLMService, "generate_completion", new=completion_mock),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return _run_enrich(
            self.db, AutoEnrichRequest(connection_id=self.conn.id, use_llm=True)
        )

    def test_wide_table_is_chunked_and_all_columns_get_ai(self):
        prompts = []

        async def _per_chunk(prompt, *args, **kwargs):
            prompts.append(prompt)
            return _payload_for_prompt(prompt)

        resp = self._run_with_llm(AsyncMock(side_effect=_per_chunk))

        # 13 columnas / lote 6 -> 3 llamadas (6 + 6 + 1).
        self.assertEqual(len(prompts), 3, "la tabla ancha debe ir por lotes")
        self.assertEqual(resp.llm_enriched_count, self.WIDE_COLUMNS)
        self.assertIn(f"La IA describió {self.WIDE_COLUMNS} campo(s)", resp.message)
        self.assertFalse(resp.llm_stopped_early)

    def test_failed_chunk_does_not_condemn_the_rest_of_the_table(self):
        calls = {"n": 0}

        async def _first_chunk_garbage(prompt, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return None  # respuesta inusable del primer lote
            return _payload_for_prompt(prompt)

        resp = self._run_with_llm(AsyncMock(side_effect=_first_chunk_garbage))

        # El primer lote (6 columnas) se pierde; los otros dos se aplican.
        self.assertEqual(resp.llm_enriched_count, self.WIDE_COLUMNS - 6)
        self.assertIn(f"La IA describió {self.WIDE_COLUMNS - 6} campo(s)", resp.message)

    def test_budget_cut_is_reported_as_stopped_early(self):
        with patch("app.modules.catalog.services.catalog_enricher.LLM_TOTAL_BUDGET_S", 0.0):
            resp = self._run_with_llm(AsyncMock(return_value=_llm_payload()))

        self.assertTrue(resp.llm_stopped_early)
        self.assertEqual(resp.llm_enriched_count, 0)
        self.assertIn("no alcanzó a responder dentro del límite de tiempo", resp.message)


if __name__ == "__main__":
    unittest.main()

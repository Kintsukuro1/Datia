"""Perfil de columna para el diccionario IA y politica segura de muestras.

Cubre la decision A: el LLM recibe un perfil calculado sobre todas las filas,
pero las columnas con politica MASKED/BLOCKED no exponen valores crudos.
"""
import asyncio
import json
import time
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from sqlalchemy import text

from conftest import (
    create_temp_pg_database,
    drop_temp_pg_database,
    pg_source_conn_attrs,
    pg_source_engine,
)

from app.core.database import SessionLocal
from app.modules.admin_catalog.models import (
    ColumnPermissionType,
    CorporateConnection,
    RoleColumnPermission,
    SemanticCatalog,
)
from app.modules.admin_catalog.schemas import AutoEnrichRequest
from app.modules.auth.models import Role
from app.modules.catalog.services import enrich_estimator
from app.modules.catalog.services.catalog_enricher import CatalogEnricher
from app.modules.catalog.services.schema_inspector import SchemaInspector
from app.modules.chat_engine.llm_service import LLMService
from app.modules.system.health_service import HealthService

TABLE = '"ASRM25"'
ROWS = [
    (20240115, "ACME S.A.", 125000.50, "CL", "ASRM25-001"),
    (20240220, "GLOBEX", 98000.00, "PE", "ASRM25-002"),
    (20240305, "INITECH", 45000.75, "CL", "ASRM25-003"),
    (20240410, "ACME S.A.", 75000.00, "AR", "ASRM25-004"),
]


class TestColumnProfile(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.pg_db = create_temp_pg_database("colprofile")
        eng = pg_source_engine(cls.pg_db)
        try:
            with eng.begin() as c:
                c.execute(text(f'CREATE TABLE {TABLE} ("f1" BIGINT, "f2" TEXT, "f3" NUMERIC, "f4" TEXT, "f5" TEXT)'))
                for row in ROWS:
                    c.execute(
                        text(
                            f'INSERT INTO {TABLE} ("f1","f2","f3","f4","f5") '
                            "VALUES (:f1,:f2,:f3,:f4,:f5)"
                        ),
                        {"f1": row[0], "f2": row[1], "f3": row[2], "f4": row[3], "f5": row[4]},
                    )
        finally:
            eng.dispose()

        cls.db = SessionLocal()
        cls.conn = CorporateConnection(
            name=f"profile_{uuid.uuid4().hex[:8]}",
            **pg_source_conn_attrs(cls.pg_db),
            is_active=True,
            is_uploaded=True,
        )
        cls.db.add(cls.conn)
        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.db.query(SemanticCatalog).filter(
            SemanticCatalog.connection_id == cls.conn.id
        ).delete(synchronize_session=False)
        cls.db.delete(cls.conn)
        cls.db.commit()
        cls.db.close()
        drop_temp_pg_database(cls.pg_db)

    def _table(self, sensitive=None):
        tables = SchemaInspector.introspect_connection_metadata(
            self.conn,
            include_profile=True,
            sensitive_columns=sensitive or set(),
        )
        return tables[0]

    def test_perfil_calcula_sobre_todas_las_filas(self):
        table = self._table()
        self.assertEqual(table["row_count"], 4)
        cols = {c["name"].lower(): c for c in table["columns"]}

        f1 = cols["f1"]["profile"]
        self.assertEqual(f1["distintos"], 4)
        self.assertEqual(f1["patron"], "8 digitos (posible DDMMYYYY)")
        self.assertIn("20240115", f1["muestras"])

        f2 = cols["f2"]["profile"]
        top = {item["valor"]: item["n"] for item in f2["top"]}
        self.assertEqual(top["ACME S.A."], 2)

        f3 = cols["f3"]["profile"]
        self.assertEqual(f3["tipo"], "NUMERIC")
        self.assertAlmostEqual(f3["promedio"], 85750.31, places=2)

        f4 = cols["f4"]["profile"]
        self.assertIn("CL", {item["valor"] for item in f4["top"]})

    def test_prompt_incluye_perfil_y_no_solo_nombres(self):
        table = self._table()
        prompt = CatalogEnricher._build_table_prompt(
            table["table_name"], table["columns"][:2], table
        )
        self.assertIn("Columnas (nombre | tipo):", prompt)
        self.assertIn("Perfil calculado sobre todos los datos disponibles", prompt)
        self.assertIn("top:", prompt)
        self.assertIn("muestras:", prompt)
        # No debe nombrar las columnas que no estan en el lote: el modelo
        # terminaba describiendolas y truncando el JSON.
        self.assertNotIn("f5", prompt.lower())

    def test_parsea_array_truncado_rescatando_objetos_completos(self):
        raw = (
            '[{"column": "f1", "friendly_name": "Fecha", '
            '"description": "Fecha de la operacion.", "business_formula": "f1", '
            '"confidence": "alta"},'
            '{"column": "f2", "friendly_name": "Cliente", '
            '"description": "Nombre del cliente.", "business_formula": "f2", '
            '"confidence": "alta"},'
            '{"column": "f3", "friendly_name": "Monto", "description": "cort'
        )
        entries = CatalogEnricher._parse_table_entries(raw)
        self.assertEqual(set(entries), {"f1", "f2"})
        self.assertEqual(entries["f2"]["friendly_name"], "Cliente")

    def test_columna_sensible_no_expone_valores(self):
        table = self._table(sensitive={("asrm25", "f2")})
        cols = {c["name"].lower(): c for c in table["columns"]}
        f2 = cols["f2"]
        self.assertEqual(f2["sample_values"], [])
        self.assertTrue(f2["profile"]["valores_omitidos"])
        self.assertNotIn("top", f2["profile"])
        self.assertNotIn("muestras", f2["profile"])
        self.assertNotIn("min", f2["profile"])
        self.assertNotIn("max", f2["profile"])

        prompt = CatalogEnricher._build_table_prompt(
            table["table_name"], table["columns"], table
        )
        self.assertNotIn("ACME", prompt)
        self.assertIn("valores omitidos", prompt)

    def test_anti_fuga_detecta_valor_literal(self):
        self.assertTrue(
            CatalogEnricher._contains_sample_value(
                "El cliente ACME S.A. aparece en el registro.", ["ACME S.A."]
            )
        )
        self.assertFalse(
            CatalogEnricher._contains_sample_value(
                "Cantidad de clientes distintos.", ["ACME S.A."]
            )
        )

    def test_solo_acepta_formulas_sql(self):
        self.assertTrue(CatalogEnricher._is_sql_formula("SUM(f3)"))
        self.assertTrue(CatalogEnricher._is_sql_formula("TO_DATE(f1::text, 'DDMMYYYY')"))
        self.assertFalse(CatalogEnricher._is_sql_formula("CONCATENAR(DIA(f1), MES(f1))"))
        self.assertFalse(CatalogEnricher._is_sql_formula("Sumatoria de los valores"))
        self.assertFalse(CatalogEnricher._is_sql_formula("N/A"))

    def test_formula_de_fecha_tiene_que_parsear_no_formatear(self):
        profile = {"patron": "8 digitos (posible DDMMYYYY)"}
        self.assertFalse(CatalogEnricher._formula_is_valid_for_profile(
            "TO_CHAR(f1, 'DDMMYYYY')", profile
        ))
        self.assertTrue(CatalogEnricher._formula_is_valid_for_profile(
            "TO_DATE(LPAD(f1::text, 8, '0'), 'DDMMYYYY')", profile
        ))
        self.assertTrue(CatalogEnricher._formula_is_valid_for_profile(
            "SUM(f3)", {"patron": "alfanumerico"}
        ))


class TestEnricherProfilePolicy(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.pg_db = create_temp_pg_database("enrichprofile")
        eng = pg_source_engine(cls.pg_db)
        try:
            with eng.begin() as c:
                c.execute(text(f'CREATE TABLE {TABLE} ("f1" BIGINT, "f2" TEXT, "f3" NUMERIC, "f4" TEXT, "f5" TEXT)'))
                for row in ROWS:
                    c.execute(
                        text(
                            f'INSERT INTO {TABLE} ("f1","f2","f3","f4","f5") '
                            "VALUES (:f1,:f2,:f3,:f4,:f5)"
                        ),
                        {"f1": row[0], "f2": row[1], "f3": row[2], "f4": row[3], "f5": row[4]},
                    )
        finally:
            eng.dispose()

    @classmethod
    def tearDownClass(cls):
        drop_temp_pg_database(cls.pg_db)

    def setUp(self):
        self.db = SessionLocal()
        enrich_estimator._SPEED_CACHE[("", "")] = (10.0, time.time())
        self.conn = CorporateConnection(
            name=f"enrich_profile_{uuid.uuid4().hex[:8]}",
            **pg_source_conn_attrs(self.pg_db),
            is_active=True,
            is_uploaded=True,
        )
        self.db.add(self.conn)
        self.db.commit()

        self.role = Role(
            name=f"Rol perfil {uuid.uuid4().hex[:8]}",
            description="rol de prueba para politica de muestras",
        )
        self.db.add(self.role)
        self.db.commit()

        self.db.add(RoleColumnPermission(
            role_id=self.role.id,
            connection_id=self.conn.id,
            schema_name="public",
            table_name="ASRM25",
            column_name="f2",
            permission_type=ColumnPermissionType.MASKED,
        ))
        self.db.commit()

    def tearDown(self):
        self.db.query(SemanticCatalog).filter(
            SemanticCatalog.connection_id == self.conn.id
        ).delete(synchronize_session=False)
        self.db.query(RoleColumnPermission).filter(
            RoleColumnPermission.role_id == self.role.id
        ).delete(synchronize_session=False)
        self.db.delete(self.conn)
        self.db.delete(self.role)
        self.db.commit()
        self.db.close()

    def test_enricher_oculta_muestras_de_columna_masked(self):
        payload = json.dumps([
            {
                "column": name,
                "friendly_name": f"Nombre {name}",
                "description": f"Descripcion {name}",
                "business_formula": f"SUM({name})",
                "confidence": "alta",
            }
            for name in ("f1", "f2", "f3", "f4", "f5")
        ])

        with patch.object(
            HealthService, "check_llm_connectivity",
            new_callable=AsyncMock,
            return_value={"success": True, "message": "ok"},
        ), patch.object(
            LLMService, "generate_completion",
            new=AsyncMock(return_value=payload),
        ) as mock_llm:
            resp = asyncio.run(CatalogEnricher.auto_enrich_catalog(
                self.db,
                AutoEnrichRequest(connection_id=self.conn.id, use_llm=True),
            ))

        self.assertTrue(resp.success, resp.message)
        prompt = mock_llm.await_args.kwargs.get("system_prompt")  # noqa: F841
        # La llamada al LLM recibe el prompt como primer argumento posicional.
        prompt_text = mock_llm.await_args.args[0]
        self.assertIn("Perfil calculado sobre todos los datos disponibles", prompt_text)
        self.assertIn("20240115", prompt_text)  # f1 no es sensible
        self.assertNotIn("ACME", prompt_text)    # f2 es MASKED
        self.assertIn("valores omitidos", prompt_text)

    def test_confidence_baja_conserva_heuristica(self):
        payload = json.dumps([
            {
                "column": "f1",
                "friendly_name": "No usar",
                "description": "No usar",
                "business_formula": "",
                "confidence": "baja",
            }
        ])

        with patch.object(
            HealthService, "check_llm_connectivity",
            new_callable=AsyncMock,
            return_value={"success": True, "message": "ok"},
        ), patch.object(
            LLMService, "generate_completion",
            new=AsyncMock(return_value=payload),
        ):
            resp = asyncio.run(CatalogEnricher.auto_enrich_catalog(
                self.db,
                AutoEnrichRequest(connection_id=self.conn.id, use_llm=True),
            ))

        self.assertTrue(resp.success, resp.message)
        self.assertEqual(resp.llm_enriched_count, 0)
        row = self.db.query(SemanticCatalog).filter(
            SemanticCatalog.connection_id == self.conn.id,
            SemanticCatalog.column_name == "f1",
        ).first()
        self.assertNotEqual(row.friendly_name, "No usar")
        self.assertFalse(row.is_ai_generated)

    def test_pseudocodigo_no_reemplaza_la_formula(self):
        payload = json.dumps([
            {
                "column": "f1",
                "friendly_name": "Fecha de transaccion",
                "description": "Fecha de la operacion.",
                "business_formula": "CONCATENAR(DIA(f1), MES(f1))",
                "confidence": "alta",
            }
        ])

        with patch.object(
            HealthService, "check_llm_connectivity",
            new_callable=AsyncMock,
            return_value={"success": True, "message": "ok"},
        ), patch.object(
            LLMService, "generate_completion",
            new=AsyncMock(return_value=payload),
        ):
            resp = asyncio.run(CatalogEnricher.auto_enrich_catalog(
                self.db,
                AutoEnrichRequest(connection_id=self.conn.id, use_llm=True),
            ))

        self.assertTrue(resp.success, resp.message)
        row = self.db.query(SemanticCatalog).filter(
            SemanticCatalog.connection_id == self.conn.id,
            SemanticCatalog.column_name == "f1",
        ).first()
        self.assertEqual(row.friendly_name, "Fecha de transaccion")
        self.assertNotEqual(row.business_formula, "CONCATENAR(DIA(f1), MES(f1))")

    def test_formula_con_valor_literal_se_descarta_sin_perder_el_nombre(self):
        payload = json.dumps([
            {
                "column": "f1",
                "friendly_name": "Fecha de transaccion",
                "description": "Fecha de la operacion.",
                "business_formula": "CONCATENAR(f1, '20240115')",
                "confidence": "alta",
            }
        ])

        with patch.object(
            HealthService, "check_llm_connectivity",
            new_callable=AsyncMock,
            return_value={"success": True, "message": "ok"},
        ), patch.object(
            LLMService, "generate_completion",
            new=AsyncMock(return_value=payload),
        ):
            resp = asyncio.run(CatalogEnricher.auto_enrich_catalog(
                self.db,
                AutoEnrichRequest(connection_id=self.conn.id, use_llm=True),
            ))

        self.assertTrue(resp.success, resp.message)
        row = self.db.query(SemanticCatalog).filter(
            SemanticCatalog.connection_id == self.conn.id,
            SemanticCatalog.column_name == "f1",
        ).first()
        self.assertEqual(row.friendly_name, "Fecha de transaccion")
        self.assertNotIn("20240115", row.business_formula or "")

    def test_descripcion_con_valor_literal_se_redacta(self):
        payload = json.dumps([
            {
                "column": "f1",
                "friendly_name": "Fecha de transaccion",
                "description": "La fecha 20240115 corresponde a la operacion.",
                "business_formula": "",
                "confidence": "alta",
            }
        ])

        with patch.object(
            HealthService, "check_llm_connectivity",
            new_callable=AsyncMock,
            return_value={"success": True, "message": "ok"},
        ), patch.object(
            LLMService, "generate_completion",
            new=AsyncMock(return_value=payload),
        ):
            resp = asyncio.run(CatalogEnricher.auto_enrich_catalog(
                self.db,
                AutoEnrichRequest(connection_id=self.conn.id, use_llm=True),
            ))

        self.assertTrue(resp.success, resp.message)
        row = self.db.query(SemanticCatalog).filter(
            SemanticCatalog.connection_id == self.conn.id,
            SemanticCatalog.column_name == "f1",
        ).first()
        self.assertEqual(row.friendly_name, "Fecha de transaccion")
        self.assertNotIn("20240115", row.description or "")
        self.assertIn("[valor]", row.description or "")


if __name__ == "__main__":
    unittest.main()

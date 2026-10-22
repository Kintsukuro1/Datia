import unittest
from app.modules.chat_engine.sql_executor import SQLExecutor
from app.modules.chat_engine.ast_validator import ASTValidator

class TestDynamicPrompts(unittest.TestCase):

    def test_grounding_query_generation_dynamic(self):
        """Verifica que get_grounding_query genere una consulta sobre la primera tabla autorizada."""
        allowed_tables = {"dim_clientes", "fact_ventas"}
        sql = SQLExecutor.get_grounding_query(
            question="¿Cuántos clientes tenemos?",
            user_role="Economista",
            allowed_tables=allowed_tables
        )
        self.assertIn("dim_clientes", sql)
        self.assertTrue(sql.startswith("SELECT * FROM"))

    def test_grounding_query_fallback_table(self):
        """Verifica que si ninguna tabla coincide explícitamente en el texto, se use la primera autorizada en orden alfabético."""
        allowed_tables = {"fact_inventario", "dim_almacen"}
        sql = SQLExecutor.get_grounding_query(
            question="Resumen general",
            user_role="TI",
            allowed_tables=allowed_tables
        )
        self.assertIn("dim_almacen", sql)

    def test_grounding_query_quotes_table_names(self):
        """Un nombre con comilla debe sobrevivir al guardarrail que ejecuta el grounding.

        Sin citar, `SELECT * FROM ventas"raras` no parsea para sqlglot: la rama
        conversacional quedaba sin filas de contexto para el LLM (fail closed,
        pero silencioso).
        """
        allowed_tables = {'ventas"raras'}
        sql = SQLExecutor.get_grounding_query(
            question="Resumen general",
            user_role="TI",
            allowed_tables=allowed_tables
        )
        self.assertIn('"ventas""raras"', sql)
        ASTValidator.validate_and_secure_sql(
            sql,
            dialect="postgres",
            allowed_tables=allowed_tables,
            table_columns={'ventas"raras': ["id"]},
            is_admin=False
        )

    def test_unified_synthesis_resilient_parser(self):
        """Verifica el parser resiliente de 3 capas en KPICalculator.generate_unified_synthesis_with_llm."""
        import asyncio
        from unittest.mock import patch, AsyncMock
        from app.modules.chat_engine.kpi_calculator import KPICalculator

        # Tier 1 & 2: JSON con trailing commas y subtítulos con relleno
        tier2_json = """{
            "narrative": "El total de ventas supera la meta proyectada.",
            "kpis": [
                {"title": "Total Facturado", "column": "monto", "agg": "total", "subtitle": "Este KPI indica el acumulado total.", "change_direction": "positive"},
                {"title": "Ticket Promedio", "column": "monto", "agg": "avg", "subtitle": "Este KPI muestra la rentabilidad neta.", "change_direction": "positive"},
                {"title": "Monto Maximo", "column": "monto", "agg": "max", "subtitle": "Muestra del mes corriente.", "change_direction": "neutral"},
            ],
            "executive_report": {
                "overview": "Excelente desempeño trimestral.",
                "key_findings": ["Crecimiento de 12%"],
                "recommendations": ["Expandir canales"],
                "risk_level": "BAJO",
                "business_impact": "Consolidación de cuota de mercado."
            },
            "suggested_questions": [
                "¿Cuál es el margen por categoría?",
                "¿Cómo evolucionó el costo?",
                "¿Quién es el cliente principal?"
            ],
        }"""

        with patch("app.modules.chat_engine.llm_service.LLMService.generate_completion", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = tier2_json
            result = asyncio.run(KPICalculator.generate_unified_synthesis_with_llm(
                question="¿Cómo van las ventas?",
                user_role="Economista",
                rows=[{"monto": 1500000}],
                columns=["monto"],
                secured_sql="SELECT monto FROM fact_ventas",
                is_llm_active=True
            ))

            self.assertIsNotNone(result)
            self.assertEqual(result["narrative"], "El total de ventas supera la meta proyectada.")
            self.assertEqual(len(result["kpis"]), 3)
            # Sanitization of AI filler in subtitle
            self.assertNotIn("Este KPI indica", result["kpis"][0].subtitle)
            # El VALOR lo calcula el backend desde las filas, no el modelo: el
            # prompt no pide cifras y el JSON de prueba no trae ninguna.
            self.assertEqual(result["kpis"][0].value, "$1,500,000.00")
            self.assertEqual(result["kpis"][1].value, "$1,500,000.00")
            self.assertEqual(result["executive_report"].risk_level, "BAJO")
            self.assertEqual(len(result["suggested_questions"]), 3)

        # Tier 3: LLM responde solo con texto plano/markdown en vez de JSON
        tier3_raw_text = "Resumen directo en prosa: Las ventas fueron de $1.5M sin incidentes."
        with patch("app.modules.chat_engine.llm_service.LLMService.generate_completion", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = tier3_raw_text
            result = asyncio.run(KPICalculator.generate_unified_synthesis_with_llm(
                question="¿Cómo van las ventas?",
                user_role="Economista",
                rows=[{"monto": 1500000}],
                columns=["monto"],
                secured_sql="SELECT monto FROM fact_ventas",
                is_llm_active=True
            ))

            self.assertIsNotNone(result)
            self.assertEqual(result["narrative"], tier3_raw_text)
            self.assertIsNone(result["kpis"])
            self.assertIsNone(result["executive_report"])

if __name__ == "__main__":
    unittest.main()


import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.modules.chat_engine.engine import QueryEngine

class TestSuggestionsEndpoint(unittest.TestCase):

    def test_dynamic_suggestions_economista(self):
        """Verifica que las sugerencias para Economista contengan preguntas sobre ventas e ingresos."""
        allowed_tables = {"fact_ventas", "dim_productos", "fact_ingresos_costos"}
        suggestions = QueryEngine.get_dynamic_suggestions("Economista", allowed_tables)
        
        self.assertGreaterEqual(len(suggestions), 2)
        self.assertTrue(any("ventas" in s.lower() or "ingresos" in s.lower() for s in suggestions))

    def test_dynamic_suggestions_ti(self):
        """Verifica que las sugerencias para TI contengan preguntas sobre servidores e incidentes."""
        allowed_tables = {"fact_incidentes_ti", "dim_servidores"}
        suggestions = QueryEngine.get_dynamic_suggestions("TI", allowed_tables)

        self.assertGreaterEqual(len(suggestions), 2)
        self.assertTrue(any("ti" in s.lower() or "servidor" in s.lower() or "incidentes" in s.lower() for s in suggestions))

    def test_dynamic_suggestions_empty_role(self):
        """Verifica que si no hay tablas autorizadas (ej. rol Usuario), se sugiera solicitar acceso."""
        suggestions = QueryEngine.get_dynamic_suggestions("Usuario", set())
        self.assertEqual(len(suggestions), 2)
        self.assertIn("¿Qué información puedo consultar con mi perfil?", suggestions[0])

    def test_llm_suggestions_fallback(self):
        """Verifica que el método asíncrono get_dynamic_suggestions_with_llm funcione y use fallback cuando el LLM está offline."""
        import asyncio
        allowed_tables = {"fact_ventas", "dim_productos"}
        suggestions = asyncio.run(
            QueryEngine.get_dynamic_suggestions_with_llm(
                user_role="Economista",
                allowed_tables=allowed_tables
            )
        )
        self.assertGreaterEqual(len(suggestions), 2)

    def test_builder_options_usa_columnas_reales_y_respeta_masking(self):
        """El Smart Builder no inventa columnas ni ofrece columnas MASKED/ID."""
        table_columns = {
            "ventas": [
                {"name": "numero_venta", "type": "INTEGER", "is_pk": True, "label": "Número de Venta", "is_masked": False},
                {"name": "periodo", "type": "VARCHAR(7)", "is_pk": False, "label": "Periodo Fiscal", "is_masked": False},
                {"name": "fecha_compra", "type": "DATE", "is_pk": False, "label": "Fecha de compra", "is_masked": False},
                {"name": "tipo_filamento", "type": "VARCHAR(30)", "is_pk": False, "label": "Tipo de filamento", "is_masked": False},
                {"name": "cantidad_productos", "type": "SMALLINT", "is_pk": False, "label": "Cantidad de productos", "is_masked": False},
                {"name": "monto_total", "type": "INTEGER", "is_pk": False, "label": "Monto Total de Venta", "is_masked": False},
                {"name": "rut_comprador", "type": "VARCHAR(12)", "is_pk": False, "label": "RUT Comprador", "is_masked": True},
            ]
        }

        tables = QueryEngine.build_builder_options(table_columns, {"ventas": "Ventas"})

        self.assertEqual(len(tables), 1)
        table = tables[0]
        self.assertEqual(table["label"], "Ventas")
        # Monto primero: es la metrica de negocio esperada, no el primer entero.
        self.assertEqual(table["metrics"][0]["column"], "monto_total")
        self.assertEqual(table["metrics"][-1]["aggregate"], "count")
        metric_names = [m["column"] for m in table["metrics"]]
        dimension_names = [d["name"] for d in table["dimensions"]]
        date_names = [d["name"] for d in table["dates"]]
        self.assertNotIn("numero_venta", metric_names)
        self.assertNotIn("rut_comprador", dimension_names)
        self.assertIn("tipo_filamento", dimension_names)
        self.assertIn("fecha_compra", date_names)
        self.assertIn("periodo", date_names)

    def test_llm_descarta_sugerencias_que_no_estan_ancladas_al_esquema(self):
        """Una alucinacion del modelo no llega a la pantalla del usuario."""
        table_columns = {
            "ventas": [
                {"name": "periodo", "type": "VARCHAR(7)", "is_pk": False, "label": "Periodo Fiscal", "is_masked": False},
                {"name": "fecha_compra", "type": "DATE", "is_pk": False, "label": "Fecha de compra", "is_masked": False},
                {"name": "tipo_filamento", "type": "VARCHAR(30)", "is_pk": False, "label": "Tipo de filamento", "is_masked": False},
                {"name": "tipo_documento", "type": "VARCHAR(20)", "is_pk": False, "label": "Tipo de Documento", "is_masked": False},
                {"name": "monto_total", "type": "INTEGER", "is_pk": False, "label": "Monto Total de Venta", "is_masked": False},
            ]
        }
        llm_text = "\n".join([
            "📊 ¿Cuál es la suma de Monto Total de Venta por Tipo de filamento?",
            "💡 ¿Cuánto gasta Recursos Humanos en capacitación?",
            "📋 ¿Cuántos registros hay por Tipo de Documento?",
            "📅 ¿Cómo evoluciona Monto Total de Venta por Periodo Fiscal?",
        ])

        with patch(
            "app.modules.chat_engine.suggestions_service.LLMService.generate_completion",
            new=AsyncMock(return_value=llm_text),
        ):
            suggestions = asyncio.run(QueryEngine.get_dynamic_suggestions_with_llm(
                user_role="Administrador de Plataforma",
                allowed_tables={"ventas"},
                table_columns=table_columns,
                table_labels={"ventas": "Ventas"},
            ))

        self.assertEqual(len(suggestions), 3)
        self.assertFalse(any("Recursos Humanos" in suggestion for suggestion in suggestions))

    def test_sugerencias_deterministicas_salen_del_esquema_real(self):
        """Sin LLM, las sugerencias siguen siendo ejecutables y variadas."""
        table_columns = {
            "ventas": [
                {"name": "periodo", "type": "VARCHAR(7)", "is_pk": False, "label": "Periodo Fiscal", "is_masked": False},
                {"name": "fecha_compra", "type": "DATE", "is_pk": False, "label": "Fecha de compra", "is_masked": False},
                {"name": "tipo_filamento", "type": "VARCHAR(30)", "is_pk": False, "label": "Tipo de filamento", "is_masked": False},
                {"name": "forma_pago", "type": "VARCHAR(30)", "is_pk": False, "label": "Método de Pago", "is_masked": False},
                {"name": "tipo_documento", "type": "VARCHAR(20)", "is_pk": False, "label": "Tipo de Documento", "is_masked": False},
                {"name": "monto_total", "type": "INTEGER", "is_pk": False, "label": "Monto Total de Venta", "is_masked": False},
            ]
        }

        suggestions = QueryEngine.get_dynamic_suggestions(
            "Administrador de Plataforma",
            {"ventas"},
            table_columns=table_columns,
            table_labels={"ventas": "Ventas"},
        )

        self.assertGreaterEqual(len(suggestions), 4)
        joined = " ".join(suggestions)
        self.assertIn("Monto Total de Venta", joined)
        self.assertIn("Tipo de filamento", joined)
        self.assertIn("Periodo Fiscal", joined)
        self.assertNotIn("rut", joined.lower())

if __name__ == "__main__":
    unittest.main()

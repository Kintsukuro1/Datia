"""La fecha actual del sistema llega al prompt SQL y se usa para fechas relativas."""
import unittest

from app.core.prompts import PromptManager


class TestCurrentDatePrompt(unittest.TestCase):

    def test_user_prompt_incluye_fecha_actual(self):
        prompt = PromptManager.get_text_to_sql_user_prompt(
            question="¿Cuántos registros hay este año?",
            user_role="Economista",
            schema_context="CREATE TABLE datos (campo_2 BIGINT);",
            allowed_tables={"datos"},
            current_date="2026-11-07",
        )
        self.assertIn("Fecha actual del sistema: 2026-11-07", prompt)

    def test_user_prompt_usa_la_fecha_de_hoy_por_default(self):
        from datetime import date

        prompt = PromptManager.get_text_to_sql_user_prompt(
            question="¿Qué pasó hoy?",
            user_role="Economista",
            schema_context="CREATE TABLE datos (campo_2 BIGINT);",
            allowed_tables={"datos"},
        )
        self.assertIn(f"Fecha actual del sistema: {date.today().isoformat()}", prompt)

    def test_system_prompt_exige_fechas_relativas(self):
        prompt = PromptManager.get_text_to_sql_system_prompt(
            "Economista", {"datos"}, dialect="postgres"
        )
        self.assertIn("FECHAS RELATIVAS", prompt)
        self.assertIn("FECHA ACTUAL DEL SISTEMA", prompt)
        self.assertIn("CURRENT_DATE", prompt)
        self.assertIn("No reemplaces el período pedido", prompt)


if __name__ == "__main__":
    unittest.main()

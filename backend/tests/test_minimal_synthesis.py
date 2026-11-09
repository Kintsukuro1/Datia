"""Perfil de sintesis minimal (texto-first) y perfil de datos agregado."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from app.core.prompts import PromptManager
from app.modules.chat_engine.kpi_calculator import (
    KPICalculator,
    MINIMAL_SYNTHESIS_MAX_TOKENS,
)
from app.modules.chat_engine.schemas import PresentationHints


class TestSynthesisProfileSelection(unittest.TestCase):

    def test_data_analysis_usa_minimal(self):
        self.assertEqual(
            KPICalculator.select_synthesis_profile(
                "data_analysis", PresentationHints(show_executive_report=False)
            ),
            "minimal",
        )

    def test_informe_usa_full(self):
        self.assertEqual(
            KPICalculator.select_synthesis_profile("report", PresentationHints()),
            "full",
        )
        self.assertEqual(
            KPICalculator.select_synthesis_profile(
                "data_analysis", PresentationHints(show_executive_report=True)
            ),
            "full",
        )


class TestDataProfile(unittest.TestCase):

    def test_perfil_agrega_totales_nulos_y_top(self):
        rows = [
            {"monto": 100, "cliente": "A", "nota": None},
            {"monto": 300, "cliente": "A", "nota": "x"},
            {"monto": None, "cliente": "B", "nota": None},
        ]
        data = json.loads(
            KPICalculator.build_data_profile(rows, ["monto", "cliente", "nota"])
        )

        self.assertEqual(data["filas_devueltas"], 3)
        self.assertEqual(data["columnas"]["monto"]["tipo"], "numerica")
        self.assertEqual(data["columnas"]["monto"]["nulos"], 1)
        self.assertEqual(data["columnas"]["monto"]["suma"], 400)
        self.assertEqual(data["columnas"]["monto"]["promedio"], 200)
        self.assertEqual(data["columnas"]["cliente"]["tipo"], "texto")
        self.assertEqual(
            data["columnas"]["cliente"]["top"][0], {"valor": "A", "n": 2}
        )
        self.assertEqual(data["columnas"]["nota"]["nulos"], 2)

    def test_bool_no_se_trata_como_numerico(self):
        data = json.loads(
            KPICalculator.build_data_profile(
                [{"flag": True}, {"flag": False}], ["flag"]
            )
        )
        self.assertEqual(data["columnas"]["flag"]["tipo"], "texto")

    def test_perfil_vacio(self):
        self.assertEqual(KPICalculator.build_data_profile([], ["monto"]), "")

    def test_un_solo_registro_no_grafica(self):
        kpis, chart_type, chart_option, _summary, _report = (
            KPICalculator.build_dynamic_visualization(
                "¿Cuántos hay?", ["total"], [{"total": 42}], "Economista"
            )
        )
        self.assertEqual(chart_type, "none")
        self.assertEqual(chart_option, {"series": []})
        self.assertEqual(len(kpis), 3)


class TestMinimalPrompt(unittest.TestCase):

    def test_columnas_bloqueadas_no_van_al_prompt_de_sintesis(self):
        from app.modules.chat_engine.engine import QueryEngine

        disponibles = QueryEngine._available_columns_for_prompt(
            {"datos": ["campo_1", "RUT", "monto"]}, {"rut"}
        )
        self.assertEqual(disponibles, ["campo_1", "monto"])

    def test_filtra_botones_con_columnas_o_dimensiones_inexistentes(self):
        from app.modules.chat_engine.engine import QueryEngine

        preguntas = [
            "¿Cómo se distribuye por país de origen?",
            "¿Cuál es el total de campo_8?",
            "¿Cuáles son los 3 navieros con más registros?",
            "¿Cuáles son los 3 navieros con más registros?",
            "¿Qué porcentaje representan los 5 primeros?",
            "¿Cómo evoluciona campo_6 por mes?",
        ]
        filtradas = QueryEngine._filter_suggested_questions(
            preguntas, ["campo_1", "campo_6", "cantidad_registros"]
        )
        self.assertEqual(filtradas, [
            "¿Cuáles son los 3 navieros con más registros?",
            "¿Qué porcentaje representan los 5 primeros?",
            "¿Cómo evoluciona campo_6 por mes?",
        ])

    def test_dimension_que_existe_en_el_esquema_se_conserva(self):
        from app.modules.chat_engine.engine import QueryEngine

        filtradas = QueryEngine._filter_suggested_questions(
            ["¿Cuáles son los 10 clientes con más ventas?"],
            ["cliente", "ventas", "monto"],
        )
        self.assertEqual(
            filtradas, ["¿Cuáles son los 10 clientes con más ventas?"]
        )

    def test_system_pide_narrativa_y_preguntas_sin_kpis(self):
        system = PromptManager.get_unified_synthesis_system_prompt(
            "Economista", profile="minimal"
        )

        self.assertIn("suggested_questions", system)
        self.assertIn("No incluyas KPIs", system)
        self.assertIn("No uses listas numeradas", system)
        self.assertNotIn("executive_report", system)

    def test_user_incluye_perfil_y_muestra(self):
        prompt = PromptManager.get_unified_synthesis_user_prompt(
            question="¿Cómo van las ventas?",
            user_role="Economista",
            rows=[{"monto": 100}],
            columns=["monto"],
            secured_sql="SELECT monto FROM fact_ventas",
            profile="minimal",
            data_profile='{"filas_devueltas": 1}',
            available_columns=["monto", "cliente"],
        )

        self.assertIn("Perfil agregado", prompt)
        self.assertIn("<datos_base>", prompt)
        self.assertIn("narrative y suggested_questions", prompt)
        self.assertIn("Columnas disponibles en el esquema", prompt)
        self.assertIn("no inventes dimensiones", prompt)
        self.assertNotIn("executive_report", prompt)


class TestMinimalSynthesisCall(unittest.TestCase):

    def test_ignora_kpis_e_informe_aunque_el_modelo_los_mande(self):
        payload = json.dumps({
            "narrative": "La tabla tiene 3 filas y un monto total de 6.",
            "kpis": [
                {"title": "Total", "column": "monto", "agg": "total"},
            ],
            "executive_report": {
                "overview": "Este informe no debe usarse en modo minimal.",
            },
            "suggested_questions": [
                "¿Cómo evoluciona por mes?",
                "¿Cuál es el máximo?",
                "¿Qué clientes concentran el monto?",
                "¿Hay nulos en monto?",
                "Genera un informe ejecutivo.",
            ],
        })

        with patch(
            "app.modules.chat_engine.llm_service.LLMService.generate_completion",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = payload
            result = asyncio.run(KPICalculator.generate_unified_synthesis_with_llm(
                question="¿Cuánto hay?",
                user_role="Economista",
                rows=[{"monto": 1}, {"monto": 2}, {"monto": 3}],
                columns=["monto"],
                secured_sql="SELECT monto FROM fact_ventas",
                is_llm_active=True,
                profile="minimal",
                data_profile='{"filas_devueltas": 3}',
            ))

        self.assertIsNotNone(result)
        self.assertEqual(
            result["narrative"], "La tabla tiene 3 filas y un monto total de 6."
        )
        self.assertIsNone(result["kpis"])
        self.assertIsNone(result["executive_report"])
        self.assertEqual(len(result["suggested_questions"]), 5)

        kwargs = mock_llm.await_args.kwargs
        self.assertEqual(kwargs["max_tokens"], MINIMAL_SYNTHESIS_MAX_TOKENS)
        self.assertIn("suggested_questions", kwargs["system_prompt"])
        self.assertNotIn("executive_report", kwargs["system_prompt"])


if __name__ == "__main__":
    unittest.main()

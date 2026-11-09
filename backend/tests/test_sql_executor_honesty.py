"""Sin fallback de emergencia: si el SQL y el self-healing fallan, no hay filas."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.modules.chat_engine.sql_executor import SQLExecutor

_LLM = "app.modules.chat_engine.llm_service.LLMService.generate_completion"
_PHYSICAL = "app.modules.chat_engine.dynamic_schema.DynamicSchemaPruningService.get_physical_db_tables"


class TestNoEmergencyFallback(unittest.TestCase):

    def test_dos_fallos_no_ejecutan_una_tercera_consulta_arbitraria(self):
        with patch.object(
            SQLExecutor, "execute_raw_sql", side_effect=RuntimeError("boom")
        ) as exec_mock, patch(_PHYSICAL, return_value=set()), patch(
            _LLM, new=AsyncMock(return_value="no pude corregir el SQL")
        ):
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(SQLExecutor.execute_with_self_healing(
                    target_db_path=SimpleNamespace(db_type="postgresql"),
                    question="muestrame los empleados",
                    initial_sql="SELECT a FROM t",
                    allowed_tables={"t"},
                    blocked_columns=set(),
                    table_columns_map={"t": ["a"]},
                    is_llm_active=True,
                ))

        self.assertIn("boom", str(ctx.exception))
        self.assertEqual(exec_mock.call_count, 1, "no debe haber una consulta de emergencia")


if __name__ == "__main__":
    unittest.main()

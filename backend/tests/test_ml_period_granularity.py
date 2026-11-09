"""Granularidad manual: día, semana y mes sobre el mismo motor.

La aritmética de calendario y el SQL se prueban sin base de datos. Los casos de
`datos` (31 días) y `fact_ventas` (9 meses) se verifican en la corrida manual,
porque dependen de la conexión real.
"""

import unittest
from datetime import date

from app.modules.chat_engine.ast_validator import ASTValidator
from app.modules.chat_engine.forecast_calculator import (
    normalize_period,
    next_period,
    period_distance,
)
from app.modules.chat_engine.forecast_service import (
    build_group_series_sql,
    build_series_sql,
)
from app.modules.ml_forecast import ml_service


def _assert_sql(sql, columns=None):
    ok, secured, meta = ASTValidator.validate_and_secure_sql(
        sql,
        dialect="postgres",
        allowed_tables={"movimientos"},
        blocked_columns=set(),
        table_columns={"movimientos": columns or ["fecha", "valor"]},
        max_limit=1000,
        is_admin=True,
    )
    assert ok is True, meta
    return secured


class TestPeriodos(unittest.TestCase):
    def test_mes_sigue_siendo_el_default(self):
        self.assertEqual(normalize_period("2026-01-15"), "2026-01")
        self.assertEqual(normalize_period("2026/1"), "2026-01")
        self.assertEqual(normalize_period(date(2026, 1, 15)), "2026-01")
        self.assertEqual(next_period("2026-12"), "2027-01")
        self.assertEqual(period_distance("2025-12", "2026-02"), 2)

    def test_dia_normaliza_y_avanza(self):
        self.assertEqual(normalize_period("2026-1-5", "day"), "2026-01-05")
        self.assertEqual(normalize_period("2026/01/05", "day"), "2026-01-05")
        self.assertEqual(
            normalize_period("2026-01-05T10:00:00", "day"), "2026-01-05"
        )
        self.assertEqual(normalize_period(date(2026, 1, 5), "day"), "2026-01-05")
        self.assertIsNone(normalize_period("2026-01", "day"))
        self.assertIsNone(normalize_period("ene 2026", "day"))
        self.assertEqual(next_period("2026-12-31", "day"), "2027-01-01")
        self.assertEqual(
            period_distance("2026-01-01", "2026-01-31", "day"), 30
        )

    def test_semana_iso_normaliza_y_avanza(self):
        self.assertEqual(normalize_period("2026-W01", "week"), "2026-W01")
        self.assertEqual(normalize_period("2026-w1", "week"), "2026-W01")
        self.assertEqual(normalize_period("2026-01-01", "week"), "2026-W01")
        self.assertEqual(normalize_period("2025-12-29", "week"), "2026-W01")
        self.assertIsNone(normalize_period("2026-01", "week"))
        self.assertIsNone(normalize_period("2025-W53", "week"))
        self.assertEqual(next_period("2025-W52", "week"), "2026-W01")
        self.assertEqual(next_period("2026-W53", "week"), "2027-W01")
        self.assertEqual(
            period_distance("2025-W52", "2026-W01", "week"), 1
        )
        self.assertEqual(
            period_distance("2026-W01", "2026-W03", "week"), 2
        )


class TestSqlPorGranularidad(unittest.TestCase):
    def test_dia_usa_to_char_de_dia(self):
        sql = build_series_sql(
            "movimientos", "fecha", "valor",
            granularity="day", diagnostic_periods=True,
        )
        self.assertIn('TO_CHAR("fecha", \'YYYY-MM-DD\')', sql)
        self.assertIn("MAX(\"fecha\")", sql)
        _assert_sql(sql)

    def test_semana_usa_iso_year_week(self):
        sql = build_series_sql(
            "movimientos", "fecha", "valor",
            granularity="week", diagnostic_periods=True,
        )
        self.assertIn("IYYY-", sql)
        self.assertIn("IW", sql)
        _assert_sql(sql)

    def test_mes_sigue_igual(self):
        sql = build_series_sql(
            "movimientos", "fecha", "valor",
            granularity="month", diagnostic_periods=True,
        )
        self.assertIn('TO_CHAR("fecha", \'YYYY-MM\')', sql)
        _assert_sql(sql)

    def test_desglose_usa_la_misma_granularidad(self):
        sql = build_group_series_sql(
            "movimientos", "fecha", "valor", "complemento", "A",
            granularity="day",
        )
        self.assertIn("'YYYY-MM-DD'", sql)
        _assert_sql(sql, columns=["fecha", "valor", "complemento"])

    def test_matriz_de_granularidad_y_agregacion(self):
        """Las dos dimensiones son independientes: 3 granularidades x 4 aggs."""
        for granularity, period_token in (
            ("day", "YYYY-MM-DD"),
            ("week", "IYYY-"),
            ("month", "YYYY-MM"),
        ):
            for agg, agg_token in (
                ("sum", "SUM("),
                ("count", "COUNT(*)"),
                ("count_distinct", "COUNT(DISTINCT"),
                ("formula", "CASE WHEN"),
            ):
                value_expr = (
                    "CASE WHEN valor > 0 THEN valor ELSE 0 END"
                    if agg == "formula" else None
                )
                with self.subTest(granularity=granularity, agg=agg):
                    sql = build_series_sql(
                        "movimientos", "fecha", "valor",
                        granularity=granularity, agg=agg,
                        value_expr=value_expr,
                    )
                    self.assertIn(period_token, sql)
                    self.assertIn(agg_token, sql)
                    _assert_sql(sql)


class TestPreparacionDePeriodos(unittest.TestCase):
    def test_dia_descarta_el_futuro_y_conserva_el_actual(self):
        rows = [
            {"periodo": "2026-10-13", "ultima_fecha": "2026-10-13", "dias_con_datos": 1},
            {"periodo": "2026-10-14", "ultima_fecha": "2026-10-14", "dias_con_datos": 1},
            {"periodo": "2026-10-15", "ultima_fecha": "2026-10-15", "dias_con_datos": 1},
            {"periodo": "2026-10-16", "ultima_fecha": "2026-10-16", "dias_con_datos": 1},
        ]
        out = ml_service._prepare_period_rows(
            rows, today=date(2026, 10, 15), granularity="day"
        )

        self.assertEqual(len(out["rows"]), 3)
        self.assertEqual(out["future_periods_excluded"], ["2026-10-16"])
        self.assertIsNone(out["partial_period_excluded"])

    def test_semana_excluye_la_semana_en_curso_incompleta(self):
        rows = [
            {"periodo": "2026-W40", "ultima_fecha": "2026-10-04", "dias_con_datos": 7},
            {"periodo": "2026-W41", "ultima_fecha": "2026-10-11", "dias_con_datos": 7},
            {"periodo": "2026-W42", "ultima_fecha": "2026-10-14", "dias_con_datos": 3},
            {"periodo": "2026-W43", "ultima_fecha": "2026-10-19", "dias_con_datos": 1},
        ]
        out = ml_service._prepare_period_rows(
            rows, today=date(2026, 10, 14), granularity="week"
        )

        self.assertEqual([row["periodo"] for row in out["rows"]], ["2026-W40", "2026-W41"])
        self.assertEqual(out["future_periods_excluded"], ["2026-W43"])
        self.assertEqual(out["partial_period_excluded"], "2026-W42")

    def test_semana_cerrada_no_se_excluye(self):
        rows = [
            {"periodo": "2026-W40", "ultima_fecha": "2026-10-04", "dias_con_datos": 7},
            {"periodo": "2026-W41", "ultima_fecha": "2026-10-11", "dias_con_datos": 7},
            {"periodo": "2026-W42", "ultima_fecha": "2026-10-18", "dias_con_datos": 7},
        ]
        out = ml_service._prepare_period_rows(
            rows, today=date(2026, 10, 18), granularity="week"
        )

        self.assertEqual(len(out["rows"]), 3)
        self.assertIsNone(out["partial_period_excluded"])

    def test_huecos_y_pasos_por_granularidad(self):
        self.assertFalse(ml_service._has_gaps(
            ["2026-10-01", "2026-10-02", "2026-10-03"], "day"
        ))
        self.assertTrue(ml_service._has_gaps(
            ["2026-10-01", "2026-10-03"], "day"
        ))
        self.assertFalse(ml_service._has_gaps(
            ["2026-W40", "2026-W41", "2026-W42"], "week"
        ))
        self.assertTrue(ml_service._has_gaps(
            ["2026-W40", "2026-W42"], "week"
        ))

        steps = [{"step": 1}, {"step": 2}]
        self.assertEqual(
            [step["period"] for step in ml_service._with_periods(
                steps, ["2026-12-30"], "day"
            )],
            ["2026-12-31", "2027-01-01"],
        )
        self.assertEqual(
            [step["period"] for step in ml_service._with_periods(
                steps, ["2025-W52"], "week"
            )],
            ["2026-W01", "2026-W02"],
        )

    def test_grupo_alineado_por_dia(self):
        rows = [
            {"periodo": "2026-10-01", "valor": 1},
            {"periodo": "2026-10-03", "valor": 3},
        ]
        values, has_gaps = ml_service._group_values(
            rows, {"2026-10-01", "2026-10-02", "2026-10-03"}, "day"
        )
        self.assertEqual(values, [1, 3])
        self.assertTrue(has_gaps)


if __name__ == "__main__":
    unittest.main()

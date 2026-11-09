"""Resolvedor genérico: clasificación multi-esquema sin base de datos.

Cada caso fabrica columnas, catálogo y perfil como los devolvería la
introspección real. Así se prueba la regla, no una tabla concreta: si mañana
cambia el catálogo de la demo, estos tests siguen midiendo el resolvedor.
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from app.modules.catalog.services.schema_inspector import SchemaInspector
from app.modules.chat_engine.forecast_service import ForecastUnavailable
from app.modules.ml_forecast import ml_service
from app.modules.ml_forecast.ml_models import build_projection
from app.modules.ml_forecast.resolver import (
    classify_formula_kind,
    formula_columns,
    formula_is_safe,
    pick_table,
    public_option,
    public_tables,
    resolve_columns,
)


def _col(name, col_type, is_pk=False):
    return {"name": name, "type": col_type, "is_pk": is_pk}


def _profile(
    *, distintos=0, min_value=None, max_value=None, patron=None,
    top=None, muestras=None, tipo="", nulos=0,
):
    return {
        "tipo": tipo,
        "nulos": nulos,
        "distintos": distintos,
        "min": min_value,
        "max": max_value,
        "patron": patron,
        "top": top or [],
        "muestras": muestras or [],
    }


def _names(options):
    return [option["column"] for option in options]


class TestResolvedorCatalogoYPerfil(unittest.TestCase):
    def test_fecha_bigint_y_metrica_texto_por_catalogo(self):
        """El caso de una base con fechas DDMMYYYY y montos en TEXT."""
        cols = [
            _col("campo_1", "BIGINT"),
            _col("campo_2", "BIGINT"),
            _col("campo_3", "BIGINT"),
            _col("campo_6", "TEXT"),
            _col("campo_7", "TEXT"),
        ]
        catalog = {
            "campo_1": {"business_formula": "COUNT(DISTINCT campo_1)", "friendly_name": "Folio"},
            "campo_2": {
                "business_formula": "TO_DATE(LPAD(campo_2::text, 8, '0'), 'DDMMYYYY')",
                "friendly_name": "Fecha de declaración",
            },
            "campo_7": {
                "business_formula": "CASE WHEN campo_7 ~ '^[0-9]+$' THEN campo_7::numeric END",
                "friendly_name": "Peso / Valor",
            },
        }
        profiles = {
            "campo_1": _profile(distintos=36370),
            "campo_3": _profile(distintos=69, patron="alfanumerico"),
            "campo_6": _profile(distintos=800),
        }

        out = resolve_columns(cols, catalog, profiles, row_count=38180)

        self.assertEqual(_names(out["date_options"]), ["campo_2"])
        self.assertNotIn("campo_2", _names(out["metric_options"]))
        self.assertEqual(
            _names(out["metric_options"]), ["campo_7", "campo_3"]
        )
        self.assertEqual(out["metric_options"][0]["source"], "catalogo")
        self.assertEqual(out["metric_options"][1]["confidence"], "baja")
        self.assertEqual(_names(out["identifier_options"]), ["campo_1"])
        self.assertEqual(out["identifier_options"][0]["agg"], "count_distinct")
        self.assertEqual(out["dimension_columns"], ["campo_6"])
        self.assertIsNotNone(out["count_option"])
        self.assertEqual(out["count_option"]["agg"], "count")

    def test_identificador_no_se_ofrece_para_suma(self):
        """Un folio numérico no es una cantidad: va a `count_distinct`."""
        cols = [
            _col("id_cliente", "BIGINT"),
            _col("monto", "NUMERIC"),
        ]
        profiles = {
            "id_cliente": _profile(distintos=100),
            "monto": _profile(distintos=100),
        }

        out = resolve_columns(cols, {}, profiles, row_count=100)

        self.assertEqual(_names(out["metric_options"]), ["monto"])
        self.assertEqual(_names(out["identifier_options"]), ["id_cliente"])
        self.assertEqual(out["identifier_options"][0]["agg"], "count_distinct")

    def test_metrica_con_cardinalidad_alta_sigue_siendo_metrica(self):
        """Una serie corta puede tener un valor distinto por fila y ser una métrica."""
        cols = [_col("valor", "NUMERIC")]
        out = resolve_columns(
            cols, {}, {"valor": _profile(distintos=9)}, row_count=9
        )

        self.assertEqual(_names(out["metric_options"]), ["valor"])
        self.assertEqual(out["metric_options"][0]["agg"], "sum")
        self.assertEqual(out["identifier_options"], [])

    def test_fechas_iso_y_numeros_en_texto_por_perfil(self):
        """Sin catálogo: el perfil detecta fecha y números guardados como texto."""
        cols = [
            _col("fecha", "TEXT"),
            _col("monto", "TEXT"),
        ]
        profiles = {
            "fecha": _profile(
                distintos=9,
                min_value="2026-01-01",
                max_value="2026-09-30",
                patron="fecha ISO YYYY-MM-DD",
            ),
            "monto": _profile(
                distintos=9,
                top=[{"valor": "100", "n": 1}, {"valor": "200", "n": 1}],
                muestras=["300", "400"],
            ),
        }

        out = resolve_columns(cols, {}, profiles, row_count=9)

        self.assertEqual(_names(out["date_options"]), ["fecha"])
        self.assertEqual(out["date_options"][0]["source"], "perfil")
        self.assertEqual(out["date_options"][0]["confidence"], "media")
        self.assertIn("CAST", out["date_options"][0]["expression"])
        self.assertEqual(_names(out["metric_options"]), ["monto"])
        self.assertIn("CASE WHEN", out["metric_options"][0]["expression"])
        self.assertIn("NUMERIC", out["metric_options"][0]["expression"])

    def test_fecha_ddmmyyyy_por_perfil_sin_catalogo(self):
        """El perfil también reconoce 7-8 dígitos con rango de fecha válido."""
        cols = [_col("fecha_num", "BIGINT")]
        profiles = {
            "fecha_num": _profile(
                distintos=31,
                min_value="1122011",
                max_value="31122011",
                patron="7-8 digitos (posible DDMMYYYY)",
            )
        }

        out = resolve_columns(cols, {}, profiles, row_count=100)

        self.assertEqual(_names(out["date_options"]), ["fecha_num"])
        self.assertIn("TO_DATE", out["date_options"][0]["expression"])

    def test_formula_agregada_sobre_fecha_fisica_se_descarta(self):
        """Un `SUM(fecha)` de catálogo no convierte una DATE en métrica."""
        cols = [
            _col("fecha_venta", "DATE"),
            _col("monto_total", "NUMERIC"),
        ]
        catalog = {
            "fecha_venta": {
                "business_formula": "SUM(fecha_venta)",
                "friendly_name": "Ingreso Corporativo",
            },
            "monto_total": {
                "business_formula": "SUM(monto_total)",
                "friendly_name": "Monto Total",
            },
        }

        out = resolve_columns(cols, catalog, {}, row_count=52)

        self.assertEqual(_names(out["date_options"]), ["fecha_venta"])
        self.assertEqual(out["date_options"][0]["source"], "tipo")
        self.assertEqual(_names(out["metric_options"]), ["monto_total"])
        # `SUM(misma columna)` se trata como suma escalar, no como fórmula
        # pre-agregada: conserva el filtro `> 0` y el significado histórico.
        self.assertEqual(out["metric_options"][0]["agg"], "sum")
        self.assertEqual(out["metric_options"][0]["sql_agg"], "sum")


class TestSeleccionDeTabla(unittest.TestCase):
    TABLES = [
        {"name": "fact_ventas", "row_count": 52},
        {"name": "fact_ingresos_costos", "row_count": 8},
        {"name": "dim_clientes", "row_count": 6},
    ]

    def test_el_nombre_de_la_tabla_en_la_pregunta_gana(self):
        self.assertEqual(
            pick_table("Proyecta las ventas de los próximos 6 meses", self.TABLES, {}),
            ("fact_ventas", "catalogo"),
        )
        self.assertEqual(
            pick_table("Proyecta los ingresos de los próximos 6 meses", self.TABLES, {}),
            ("fact_ingresos_costos", "catalogo"),
        )

    def test_el_catalogo_de_tabla_desempata(self):
        tables = [{"name": "datos", "row_count": 38180}]
        catalog = {
            "datos": {"friendly_name": "Declaraciones de comercio exterior"}
        }
        self.assertEqual(
            pick_table("Proyecta las declaraciones", tables, catalog),
            ("datos", "catalogo"),
        )

    def test_sin_coincidencia_elige_la_mayor(self):
        self.assertEqual(
            pick_table("Proyecta algo sin nombre de tabla", self.TABLES, {}),
            ("fact_ventas", "mayor"),
        )

    def test_empate_prioriza_la_tabla_de_hechos(self):
        tables = [
            {"name": "dim_clientes", "row_count": 100},
            {"name": "fact_ventas", "row_count": 10},
        ]
        self.assertEqual(
            pick_table("ventas por clientes", tables, {}),
            ("fact_ventas", "catalogo"),
        )

    def test_public_tables_conserva_el_nombre_de_negocio(self):
        public = public_tables(
            [{"name": "datos", "row_count": 10}],
            {"datos": {"friendly_name": "Declaraciones"}},
        )
        self.assertEqual(
            public,
            [{"name": "datos", "row_count": 10, "friendly_name": "Declaraciones"}],
        )


class TestResolvedorSinFechaNiMetrica(unittest.TestCase):
    def test_tabla_sin_fecha_no_ofrece_count(self):
        cols = [_col("monto", "NUMERIC")]
        out = resolve_columns(
            cols, {}, {"monto": _profile(distintos=5)}, row_count=10
        )

        self.assertEqual(out["date_options"], [])
        self.assertIsNone(out["count_option"])
        self.assertEqual(_names(out["metric_options"]), ["monto"])

    def test_tabla_de_solo_texto_no_inventa_metrica(self):
        cols = [_col("nombre", "TEXT"), _col("nota", "TEXT")]
        profiles = {
            "nombre": _profile(distintos=5),
            "nota": _profile(distintos=5),
        }
        out = resolve_columns(cols, {}, profiles, row_count=10)

        self.assertEqual(out["date_options"], [])
        self.assertEqual(out["metric_options"], [])
        self.assertIsNone(out["count_option"])
        self.assertEqual(out["dimension_columns"], ["nombre", "nota"])

    def test_count_estrella_aparece_si_hay_fecha(self):
        cols = [_col("fecha", "DATE"), _col("monto", "NUMERIC")]
        out = resolve_columns(
            cols, {}, {"monto": _profile(distintos=5)}, row_count=10
        )

        self.assertIsNotNone(out["count_option"])
        self.assertEqual(out["count_option"]["column"], "*")
        self.assertEqual(out["count_option"]["agg"], "count")


class TestFormulasDelCatalogo(unittest.TestCase):
    def test_kind_reconoce_las_tres_formas(self):
        self.assertEqual(
            classify_formula_kind("TO_DATE(LPAD(campo_2::text, 8, '0'), 'DDMMYYYY')"),
            "date",
        )
        self.assertEqual(
            classify_formula_kind("COUNT(DISTINCT campo_1)"), "count_distinct"
        )
        self.assertEqual(classify_formula_kind("SUM(monto_total)"), "aggregate")
        self.assertEqual(
            classify_formula_kind("CASE WHEN campo_7 ~ '^[0-9]+$' THEN campo_7::numeric END"),
            "numeric",
        )
        self.assertIsNone(classify_formula_kind("ingreso - costo"))

    def test_formula_valida_pasa_el_ast(self):
        self.assertTrue(
            formula_is_safe(
                "SUM(monto_total)", "fact_ventas",
                {"monto_total"}, {"fact_ventas"}, set(),
            )
        )

    def test_formula_con_columna_fuera_de_candidatas_se_descarta(self):
        """Una fórmula que nombra una columna bloqueada o inexistente no se usa."""
        self.assertFalse(
            formula_is_safe(
                "ingreso - costo", "fact_ventas",
                {"margen_ganancia", "monto_total"}, {"fact_ventas"}, set(),
            )
        )

    def test_formula_rota_se_descarta(self):
        self.assertFalse(
            formula_is_safe(
                "SUM(", "fact_ventas", {"monto_total"}, {"fact_ventas"}, set()
            )
        )

    def test_formula_columns_ignora_funciones_y_literales(self):
        columns = formula_columns(
            "CASE WHEN campo_7 ~ '^[0-9]+$' THEN campo_7::numeric END"
        )
        self.assertEqual(columns, {"campo_7"})


class TestPerfilYPatrones(unittest.TestCase):
    def test_infer_pattern_reconoce_fechas_con_barras_y_hora(self):
        self.assertEqual(
            SchemaInspector._infer_pattern(["31/12/2011"]), "fecha DD/MM/YYYY"
        )
        self.assertEqual(
            SchemaInspector._infer_pattern(["2011/12/31"]), "fecha YYYY/MM/DD"
        )
        self.assertEqual(
            SchemaInspector._infer_pattern(["2011-12-31"]), "fecha ISO YYYY-MM-DD"
        )
        self.assertEqual(
            SchemaInspector._infer_pattern(["2011-12-31 10:00:00"]),
            "fecha-hora ISO YYYY-MM-DD HH:MM:SS",
        )

    def test_profile_column_es_publico(self):
        self.assertTrue(hasattr(SchemaInspector, "profile_column"))
        self.assertFalse(hasattr(SchemaInspector, "_profile_column"))

    def test_public_option_no_expone_la_expresion(self):
        public = public_option({
            "column": "campo_7",
            "label": "Peso / Valor",
            "source": "catalogo",
            "confidence": "alta",
            "evidence": "fórmula",
            "agg": "sum",
            "expression": "CASE WHEN ... THEN campo_7::numeric END",
            "sql_agg": "sum",
        })
        self.assertNotIn("expression", public)
        self.assertNotIn("sql_agg", public)
        self.assertEqual(public["agg"], "sum")


class TestGoldenSetMultiEsquema(unittest.TestCase):
    """Los cinco esquemas del plan, con el mismo resolvedor y sin ramas."""

    def test_esquema_date_numeric_clasico(self):
        cols = [
            _col("fecha", "DATE"),
            _col("monto", "NUMERIC"),
            _col("id_venta", "BIGINT"),
            _col("cliente", "TEXT"),
        ]
        profiles = {
            "id_venta": _profile(distintos=52),
            "cliente": _profile(distintos=6),
        }
        out = resolve_columns(cols, {}, profiles, row_count=52)

        self.assertEqual(_names(out["date_options"]), ["fecha"])
        self.assertEqual(_names(out["metric_options"]), ["monto"])
        self.assertEqual(_names(out["identifier_options"]), ["id_venta"])
        self.assertEqual(out["dimension_columns"], ["cliente"])
        self.assertIsNotNone(out["count_option"])

    def test_esquema_sap_por_tipo_y_perfil(self):
        """Nombres SAP: el tipo y la cardinalidad deciden, no el nombre."""
        cols = [
            _col("BUDAT", "DATE"),
            _col("WRBTR", "NUMERIC"),
            _col("BUKRS", "TEXT"),
            _col("BELNR", "TEXT"),
        ]
        profiles = {
            "bukrs": _profile(distintos=3),
            "belnr": _profile(distintos=100),
        }
        out = resolve_columns(cols, {}, profiles, row_count=100)

        self.assertEqual(_names(out["date_options"]), ["BUDAT"])
        self.assertEqual(_names(out["metric_options"]), ["WRBTR"])
        self.assertEqual(_names(out["identifier_options"]), ["BELNR"])
        self.assertEqual(out["dimension_columns"], ["BUKRS"])

    def test_decimal_con_cardinalidad_alta_sigue_siendo_metrica(self):
        out = resolve_columns(
            [_col("WRBTR", "NUMERIC")], {},
            {"wrbtr": _profile(distintos=100)}, row_count=100,
        )
        self.assertEqual(_names(out["metric_options"]), ["WRBTR"])
        self.assertEqual(out["identifier_options"], [])

    def test_tabla_sin_fecha_falla_con_motivo(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = object()
        cols = [{"name": "nombre", "type": "text"}, {"name": "nota", "type": "text"}]
        with self.assertRaises(ForecastUnavailable) as ctx:
            ml_service.run_projection(
                db, 1, "solo_texto", cols, "nombre", None, 6,
                "rol", False, None, {}, cols,
            )
        self.assertIn("no tiene columna de fecha", str(ctx.exception))

    def test_tabla_sin_metrica_falla_con_motivo(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = object()
        cols = [{"name": "fecha", "type": "date"}, {"name": "nombre", "type": "text"}]
        with self.assertRaises(ForecastUnavailable) as ctx:
            ml_service.run_projection(
                db, 1, "solo_texto", cols, None, "fecha", 6,
                "rol", False, None, {}, cols,
            )
        self.assertIn("no tiene columna numerica", str(ctx.exception))

    def test_regresion_de_la_serie_mensual_de_la_demo(self):
        values = [
            120500, 314000, 254600, 183500, 382000,
            269000, 208400, 352100, 121600,
        ]
        out = build_projection(values, 6)

        self.assertTrue(out["available"])
        self.assertEqual(out["method"], "mean")
        self.assertAlmostEqual(out["mape"], 67.7, places=1)
        self.assertTrue(out["beats_naive"])
        self.assertAlmostEqual(
            out["total"]["point"], 1_470_466.6666666667, places=2
        )
        self.assertEqual(out["total"]["measured_steps"], 3)

    def test_el_resolvedor_no_nombra_columnas_ni_tablas_concretas(self):
        import app.modules.ml_forecast.resolver as resolver_module

        source = Path(resolver_module.__file__).read_text(encoding="utf-8")
        for forbidden in ("campo_2", "campo_7", "fact_ventas"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()

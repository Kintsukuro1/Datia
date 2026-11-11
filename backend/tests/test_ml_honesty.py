"""Proyecciones ML: el limite de confianza viaja con el numero, o no se publica.

Que se verifica aca
-------------------
El motor (`ml_models`) ya tiene sus pruebas con numeros fijos; aca se cubre la
capa de SERVICIO que lo publica por HTTP, sin base de datos:

1. La serie real de 9 meses publica naive y lo declara (`beats_naive=False`):
   los otros tres metodos se calcularon y perdieron.
2. Los pasos sin backtest propio no se disfrazan de medidos: los pasos 1-3 con
   evidencia escasa (`reliable=False`) y los 4-6 heredando banda
   (`extrapolated=True`), con el periodo futuro calculado por la calculadora.
3. El analisis solo puede elegir columnas REALES: un nombre inventado, un tipo
   equivocado, un horizonte fuera de la lista o un JSON roto caen a la
   heuristica declarada (`analysis_source="heuristica"`). El horizonte sigue
   reglas propias: un 3/6/12 explicito manda siempre; con Auto (`None`) se
   acepta el del LLM si es valido; sin ninguno queda el default 6 declarado
   como `default`.
4. La narrativa tiene contrato y respaldo: sin LLM se publica la plantilla
   determinista, que dice los mismos hechos (metodo, periodos, MAPE, total,
   pasos sin evidencia) y nunca queda vacia.
5. La estimacion es instantanea y honesta: reusa la velocidad YA medida por el
   wizard (`medido`, con piso), y sin medicion declara 60 s `fallback` en vez de
   un numero con formato de medicion que nadie midio. Con Auto asume 12 pasos y
   publica `horizon_used`.

El contrapunto end-to-end (RBAC, enmascarado, gates) vive en
`test_ml_service_rbac.py`.
"""

import asyncio
import time
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from app.modules.chat_engine.ast_validator import ASTValidator
from app.modules.chat_engine.forecast_calculator import normalize_period
from app.modules.chat_engine.forecast_service import (
    ForecastUnavailable,
    _is_dimension_col,
    build_group_series_sql,
    build_group_totals_sql,
    build_series_sql,
    resolve_columns,
)
from app.modules.ml_forecast import ml_service
from app.modules.ml_forecast.ml_models import build_projection
from app.modules.ml_forecast.schemas import ProjectionEstimateRequest, ProjectionRequest

# La misma serie de `test_prediction_honesty.py`: 9 meses con el quiebre de mayo.
SERIE_VALORES = [
    9_761_026, 8_524_173, 7_319_140, 11_712_796, 17_599_629,
    20_179_434, 23_380_686, 20_237_007, 22_545_032,
]
PERIODOS = [f"2026-{mes:02d}" for mes in range(1, 10)]

COLS = [
    {"name": "fecha", "type": "date", "is_pk": False},
    {"name": "valor", "type": "numeric", "is_pk": False},
    {"name": "descripcion", "type": "text", "is_pk": False},
]

_LLM = "app.modules.chat_engine.llm_service.LLMService.generate_completion"


def _payload_proyectado(horizon=6):
    """Lo que `run_projection` arma sobre esta serie, sin la parte de base."""
    projection = build_projection(SERIE_VALORES, horizon)
    normalized = [normalize_period(p) for p in PERIODOS]
    return {
        **projection,
        "steps": ml_service._with_periods(projection["steps"], normalized),
        "has_gaps": ml_service._has_gaps(normalized),
    }


# --- 1 y 2: la serie real, publicada sin adornos ------------------------------

def test_la_serie_real_publica_naive_y_lo_declara():
    projection = build_projection(SERIE_VALORES, 6)
    assert projection["available"] is True
    assert projection["method"] == "naive"
    assert projection["beats_naive"] is False
    assert projection["mape"] == 9.0
    assert projection["n_periods"] == 9
    # Los tres candidatos se calcularon: el naive gano, no fue el unico.
    assert set(projection["mape_by_method"]) == {"naive", "mean", "ols_trend"}
    assert projection["mape_by_method"]["naive"] == min(projection["mape_by_method"].values())


def test_los_pasos_sin_evidencia_se_declaran_y_heredan_banda():
    steps = build_projection(SERIE_VALORES, 6)["steps"]
    assert [s["n_backtests"] for s in steps] == [3, 2, 1, 0, 0, 0]
    assert [s["extrapolated"] for s in steps] == [False, False, False, True, True, True]
    assert [s["reliable"] for s in steps] == [False] * 6
    # La banda heredada es la del ultimo paso medido, y un paso heredado no
    # publica MAPE propio: no hubo medicion de ESE paso.
    assert [s["band_pct"] for s in steps] == [15.5, 3.7, 10.5, 10.5, 10.5, 10.5]
    assert all(s["mape"] is None for s in steps[3:])


def test_los_pasos_llevan_periodo_futuro_con_cruce_de_ano():
    payload = _payload_proyectado(6)
    assert [s["period"] for s in payload["steps"]] == [
        "2026-10", "2026-11", "2026-12", "2027-01", "2027-02", "2027-03",
    ]


def test_has_gaps_detecta_hueco_y_eje_ilegible():
    assert ml_service._has_gaps([normalize_period(p) for p in PERIODOS]) is False
    sin_mayo = [p for p in PERIODOS if p != "2026-05"]
    assert ml_service._has_gaps([normalize_period(p) for p in sin_mayo]) is True
    assert ml_service._has_gaps(["2026-01", None]) is True
    assert ml_service._has_gaps([]) is False  # una serie vacia no tiene eje, no huecos


def _fila(periodo, dias, ultima=None):
    return {
        "periodo": periodo,
        "valor": 100,
        "ultima_fecha": ultima or f"{periodo}-28",
        "dias_con_datos": dias,
    }


class TestPeriodosExcluidos:
    """El mes en curso a medias y el futuro no entran al backtest."""

    def test_excluye_el_mes_en_curso_incompleto_y_declara_cual(self):
        filas = [_fila(f"2026-{mes:02d}", 20) for mes in range(6, 10)]
        filas.append(_fila("2026-10", 5, "2026-10-05"))
        out = ml_service._prepare_period_rows(filas, today=date(2026, 10, 15))
        assert [r["periodo"] for r in out["rows"]] == [
            "2026-06", "2026-07", "2026-08", "2026-09",
        ]
        assert out["partial_period_excluded"] == "2026-10"
        assert out["future_periods_excluded"] == []

    def test_include_partial_period_es_el_escape_hatch(self):
        filas = [_fila(f"2026-{mes:02d}", 20) for mes in range(6, 10)]
        filas.append(_fila("2026-10", 5, "2026-10-05"))
        out = ml_service._prepare_period_rows(
            filas, include_partial_period=True, today=date(2026, 10, 15)
        )
        assert len(out["rows"]) == 5
        assert out["partial_period_excluded"] is None

    def test_el_futuro_se_excluye_siempre(self):
        filas = [_fila(f"2026-{mes:02d}", 20) for mes in range(9, 11)]
        filas.append(_fila("2026-11", 20, "2026-11-15"))
        out = ml_service._prepare_period_rows(
            filas, include_partial_period=True, today=date(2026, 10, 15)
        )
        assert [r["periodo"] for r in out["rows"]] == ["2026-09", "2026-10"]
        assert out["future_periods_excluded"] == ["2026-11"]

    def test_una_foto_mensual_no_se_confunde_con_un_mes_parcial(self):
        """Con 1 dia por periodo, el mes en curso no esta \"a medias\": es la granularidad."""
        filas = [_fila(f"2026-{mes:02d}", 1) for mes in range(1, 10)]
        filas.append(_fila("2026-10", 1, "2026-10-01"))
        out = ml_service._prepare_period_rows(filas, today=date(2026, 10, 15))
        assert len(out["rows"]) == 10
        assert out["partial_period_excluded"] is None

    def test_cerca_del_cierre_el_mes_ya_es_historia(self):
        filas = [_fila(f"2026-{mes:02d}", 20) for mes in range(6, 10)]
        filas.append(_fila("2026-10", 5, "2026-10-28"))
        out = ml_service._prepare_period_rows(filas, today=date(2026, 10, 28))
        assert len(out["rows"]) == 5
        assert out["partial_period_excluded"] is None


def test_el_sql_de_diagnostico_pasa_el_ast_y_pide_dias():
    sql = build_series_sql(
        "movimientos", "fecha", "valor", "postgres", diagnostic_periods=True
    )
    assert "MAX(" in sql and "COUNT(DISTINCT" in sql
    assert "YYYY-MM-DD" in sql, "contar timestamps no es contar dias con datos"
    ok, secured, meta = ASTValidator.validate_and_secure_sql(
        sql,
        dialect="postgres",
        allowed_tables={"movimientos"},
        blocked_columns=set(),
        table_columns={"movimientos": ["fecha", "valor"]},
        max_limit=1000,
        is_admin=True,
    )
    assert ok is True, meta
    assert "dias_con_datos" in secured and "ultima_fecha" in secured


def test_el_sql_de_grupos_pasa_el_ast_y_escapa_comillas():
    cols = ["fecha", "valor", "complemento"]
    totals = build_group_totals_sql("movimientos", "fecha", "valor", "complemento")
    series = build_group_series_sql(
        "movimientos", "fecha", "valor", "complemento", "O'Higgins"
    )
    assert "'O''Higgins'" in series
    for sql in (totals, series):
        ok, secured, meta = ASTValidator.validate_and_secure_sql(
            sql,
            dialect="postgres",
            allowed_tables={"movimientos"},
            blocked_columns=set(),
            table_columns={"movimientos": cols},
            max_limit=1000,
            is_admin=True,
        )
        assert ok is True, meta
        assert "complemento" in secured

    assert _is_dimension_col({"name": "complemento", "type": "text"}) is True
    assert _is_dimension_col({"name": "id_cliente", "type": "text"}) is False
    assert _is_dimension_col({"name": "valor", "type": "numeric"}) is False


def _assert_sql_pasa_el_ast(sql, table="movimientos", columns=None):
    ok, secured, meta = ASTValidator.validate_and_secure_sql(
        sql,
        dialect="postgres",
        allowed_tables={table},
        blocked_columns=set(),
        table_columns={table: columns or ["fecha", "valor", "complemento"]},
        max_limit=1000,
        is_admin=True,
    )
    assert ok is True, meta
    return secured


def test_el_sql_de_conteo_no_filtra_por_valor():
    """COUNT(*) no necesita una métrica numérica ni el filtro de ingresos."""
    sql = build_series_sql("movimientos", "fecha", "valor", agg="count")
    assert "COUNT(*)" in sql
    assert "> 0" not in sql
    assert '"valor"' not in sql
    _assert_sql_pasa_el_ast(sql)


def test_el_sql_de_conteo_distinto_usa_la_columna_resuelta():
    sql = build_series_sql(
        "movimientos", "fecha", "cliente", agg="count_distinct"
    )
    assert "COUNT(DISTINCT" in sql
    assert '"cliente"' in sql
    assert "> 0" not in sql
    _assert_sql_pasa_el_ast(sql, columns=["fecha", "cliente"])


def test_el_sql_usa_las_expresiones_del_resolvedor():
    """La fórmula del catálogo reemplaza a la columna física en fecha y valor."""
    sql = build_series_sql(
        "movimientos", "fecha", "valor",
        date_expr="TO_DATE(LPAD(fecha::text, 8, '0'), 'DDMMYYYY')",
        value_expr="CASE WHEN valor ~ '^[0-9]+$' THEN valor::numeric END",
        diagnostic_periods=True,
    )
    assert "TO_DATE" in sql
    assert "CASE WHEN" in sql
    assert 'TO_CHAR("fecha"' not in sql
    _assert_sql_pasa_el_ast(sql)


def test_los_grupos_usan_la_expresion_resuelta():
    totals = build_group_totals_sql(
        "movimientos", "fecha", "valor", "complemento",
        value_expr="CASE WHEN valor ~ '^[0-9]+$' THEN valor::numeric END",
    )
    series = build_group_series_sql(
        "movimientos", "fecha", "valor", "complemento", "A",
        value_expr="CASE WHEN valor ~ '^[0-9]+$' THEN valor::numeric END",
    )
    for sql in (totals, series):
        assert "CASE WHEN" in sql
        _assert_sql_pasa_el_ast(sql)


# --- 3: las columnas las elige el backend, no el texto del LLM ----------------

def _analizar(respuesta_o_excepcion, horizonte=6, cols=COLS):
    """`horizonte=None` es Auto; un 3/6/12 es un pedido explicito."""
    if isinstance(respuesta_o_excepcion, Exception):
        mock = AsyncMock(side_effect=respuesta_o_excepcion)
    else:
        mock = AsyncMock(return_value=respuesta_o_excepcion)
    with patch(_LLM, new=mock):
        return asyncio.run(ml_service.analyze_question(
            "proyecta los ingresos", "movimientos", cols, resolve_columns(cols), horizonte))


def test_el_analisis_solo_acepta_nombres_reales():
    # El nombre inventado y el tipo equivocado no se "arreglan": esa columna
    # queda en None y `analyze_question` decide las DOS por heuristica.
    invalido = ml_service._validate_llm_choice(
        {"metric_column": "ingresos_totales", "date_column": "fecha"}, COLS)
    assert (invalido["metric_column"], invalido["date_column"]) == (None, "fecha")
    assert ml_service._validate_llm_choice(
        {"metric_column": "descripcion", "date_column": "fecha"}, COLS
    )["metric_column"] is None
    assert ml_service._validate_llm_choice(
        {"metric_column": "valor", "date_column": "descripcion"}, COLS
    )["date_column"] is None
    # Respuesta que no es un objeto: ahi si no hay nada que rescatar.
    assert ml_service._validate_llm_choice("no es un objeto", COLS) is None

    # Una eleccion valida se canonicaliza al nombre REAL de los metadatos.
    elegido = ml_service._validate_llm_choice(
        {"metric_column": "Valor", "date_column": "FECHA", "horizon": 12}, COLS)
    assert elegido == {
        "metric_column": "valor",
        "date_column": "fecha",
        "dimension_column": None,
        "horizon": 12,
    }
    # Horizonte fuera de rango: se ignora (no se recorta), decide el pedido.
    assert ml_service._validate_llm_choice(
        {"metric_column": "valor", "date_column": "fecha", "horizon": 99}, COLS
    )["horizon"] is None
    # 18 ya es un horizonte publicable: la pregunta manda y no hay lista cerrada.
    assert ml_service._validate_llm_choice(
        {"metric_column": "valor", "date_column": "fecha", "horizon": 18}, COLS
    )["horizon"] == 18


def test_la_dimension_se_valida_contra_las_columnas_permitidas():
    resolution = {
        "date_options": [{"column": "fecha"}],
        "metric_options": [{"column": "valor", "agg": "sum"}],
        "count_option": None,
        "dimension_columns": ["producto", "metodo_pago"],
    }
    elegido = ml_service._validate_llm_choice_resolved(
        {
            "metric_column": "valor",
            "date_column": "fecha",
            "dimension_column": "Producto",
        },
        resolution,
    )
    assert elegido["dimension_column"] == "producto"

    # Una columna que el RBAC no ofrecio no sale del analisis, aunque el LLM la
    # nombre: fail-closed.
    bloqueada = ml_service._validate_llm_choice_resolved(
        {
            "metric_column": "valor",
            "date_column": "fecha",
            "dimension_column": "cliente",
        },
        resolution,
    )
    assert bloqueada["dimension_column"] is None


def test_el_llm_puede_elegir_la_dimension_para_desglosar():
    resolution = {
        "date_options": [{"column": "fecha"}],
        "metric_options": [{"column": "valor", "agg": "sum"}],
        "count_option": None,
        "dimension_columns": ["producto"],
    }
    mock = AsyncMock(return_value=(
        '{"metric_column": "valor", "date_column": "fecha", '
        '"dimension_column": "producto", "horizon": 12}'
    ))
    with patch(_LLM, new=mock):
        out = asyncio.run(ml_service.analyze_question(
            "dime cuál es el producto con más ventas en el próximo año",
            "movimientos", [], {}, 12, resolution=resolution,
        ))
    assert out["dimension_column"] == "producto"
    assert out["horizon"] == 12
    # El fallback heuristico tambien resuelve un nombre real (el unico numerico).
    assert resolve_columns(COLS)["value_col"] == "valor"


def test_el_llm_no_puede_elegir_un_id_como_metrica():
    """Un correlativo numerico no es una metrica, aunque sea numerico."""
    cols = [
        {"name": "fecha", "type": "timestamp", "is_pk": False},
        {"name": "id_cliente", "type": "bigint", "is_pk": False},
        {"name": "monto", "type": "numeric", "is_pk": False},
    ]
    choice = ml_service._validate_llm_choice(
        {"metric_column": "id_cliente", "date_column": "fecha"}, cols
    )
    assert choice["metric_column"] is None
    assert choice["date_column"] == "fecha"


# La forma real de la tabla demo: la columna se llama "periodo" pero es TEXT, y
# el SQL de la serie solo sabe leer una columna de fecha de verdad.
COLS_TEXTO = [
    {"name": "fecha", "type": "timestamp", "is_pk": False},
    {"name": "periodo", "type": "text", "is_pk": False},
    {"name": "monto", "type": "bigint", "is_pk": False},
]


def test_el_horizonte_del_llm_sobrevive_a_una_columna_de_fecha_rechazada():
    """El caso vivo de la tabla demo: el modelo elige `periodo` (TEXT) como fecha.

    Esa eleccion no se puede usar (el SQL de la serie exige tipo fecha), pero el
    horizonte que el modelo extrajo de la pregunta ("12 meses") se conserva y la
    eleccion de columnas cae ENTERA a la heuristica — sin mezclar.
    """
    out = _analizar(
        '{"metric_column": "monto", "date_column": "periodo", "horizon": 12}',
        horizonte=None, cols=COLS_TEXTO)
    assert out["source"] == "heuristica"
    assert (out["metric_column"], out["date_column"]) == ("monto", "fecha")
    assert out["horizon"] == 12
    assert out["horizon_source"] == "llm"

    # Sin horizonte valido del modelo, Auto cae al default declarado.
    out = _analizar(
        '{"metric_column": "monto", "date_column": "periodo"}',
        horizonte=None, cols=COLS_TEXTO)
    assert out["horizon"] == 6
    assert out["horizon_source"] == "default"


def test_el_horizonte_explicito_manda_sobre_el_del_llm():
    """El usuario pidio 3: el modelo puede leer \"12 meses\", pero se publican 3."""
    out = _analizar(
        '{"metric_column": "valor", "date_column": "fecha", "horizon": 12}',
        horizonte=3)
    assert out["source"] == "llm"
    assert out["horizon"] == 3
    assert out["horizon_source"] == "pedido"


def test_el_horizonte_explicito_sobrevive_a_columnas_rechazadas():
    out = _analizar(
        '{"metric_column": "monto", "date_column": "periodo", "horizon": 12}',
        horizonte=6, cols=COLS_TEXTO)
    assert out["source"] == "heuristica"
    assert out["horizon"] == 6
    assert out["horizon_source"] == "pedido"


def test_el_analisis_inventado_cae_a_heuristica_declarada():
    out = _analizar('{"metric_column": "ingresos_totales", "date_column": "fecha", "horizon": 6}')
    assert out["source"] == "heuristica"
    assert (out["metric_column"], out["date_column"]) == ("valor", "fecha")
    assert out["table"] == "movimientos"
    assert out["horizon"] == 6
    assert out["horizon_source"] == "pedido"


def test_el_analisis_valido_se_publica_como_llm():
    out = _analizar(
        '{"metric_column": "valor", "date_column": "fecha", "horizon": 3}',
        horizonte=None)
    assert out["source"] == "llm"
    assert out["horizon"] == 3  # la lista cerrada lo permite
    assert out["horizon_source"] == "llm"


def test_el_analisis_con_horizonte_invalido_cae_al_default_de_auto():
    out = _analizar(
        '{"metric_column": "valor", "date_column": "fecha", "horizon": 99}',
        horizonte=None)
    assert out["source"] == "llm"
    assert out["horizon"] == 6
    assert out["horizon_source"] == "default"


def test_el_analisis_con_json_roto_o_sin_llm_cae_a_heuristica():
    assert _analizar("```json\n{roto\n```")["source"] == "heuristica"
    assert _analizar("")["source"] == "heuristica"
    assert _analizar(RuntimeError("servidor apagado"))["source"] == "heuristica"


def test_override_manual_con_horizonte_explicito_no_llama_al_llm():
    with patch(
        "app.modules.ml_forecast.ml_service._ask_llm_for_columns",
        new=AsyncMock(side_effect=AssertionError("no debe llamar al LLM")),
    ) as mock:
        out = asyncio.run(ml_service.analyze_question(
            "proyecta los ingresos", "movimientos", COLS, resolve_columns(COLS),
            3, "VALOR", "FECHA",
        ))

    assert out == {
        "table": "movimientos",
        "metric_column": "valor",
        "date_column": "fecha",
        "horizon": 3,
        "horizon_source": "pedido",
        "source": "manual",
    }
    mock.assert_not_called()


def test_override_manual_con_auto_solo_pide_el_horizonte():
    mock = AsyncMock(return_value={
        "metric_column": "descripcion", "date_column": "descripcion", "horizon": 12,
    })
    with patch(
        "app.modules.ml_forecast.ml_service._ask_llm_for_columns", new=mock
    ):
        out = asyncio.run(ml_service.analyze_question(
            "proyecta los ingresos", "movimientos", COLS, resolve_columns(COLS),
            None, "valor", "fecha",
        ))

    assert out["source"] == "manual"
    assert (out["metric_column"], out["date_column"]) == ("valor", "fecha")
    assert out["horizon"] == 12
    assert out["horizon_source"] == "llm"
    mock.assert_called_once()


def test_un_override_invalido_corta_antes_de_tocar_el_sql():
    with pytest.raises(ForecastUnavailable) as exc:
        asyncio.run(ml_service.analyze_question(
            "proyecta los ingresos", "movimientos", COLS, resolve_columns(COLS),
            6, "no_existe", "fecha",
        ))
    assert "no es una metrica numerica permitida" in str(exc.value)


def test_un_override_manual_sobrevive_a_la_fecha_invalida_del_llm():
    mock = AsyncMock(return_value={
        "metric_column": "valor", "date_column": "descripcion", "horizon": 3,
    })
    with patch(
        "app.modules.ml_forecast.ml_service._ask_llm_for_columns", new=mock
    ):
        out = asyncio.run(ml_service.analyze_question(
            "proyecta los ingresos", "movimientos", COLS, resolve_columns(COLS),
            6, "valor", None,
        ))

    assert out["source"] == "manual"
    assert out["metric_column"] == "valor"
    assert out["date_column"] == "fecha"  # heuristica, no la eleccion invalida
    assert out["horizon"] == 6


# --- 4: la narrativa tiene contrato y respaldo --------------------------------

def test_la_plantilla_dice_los_mismos_hechos_que_el_llm_podria_citar():
    texto = ml_service._template_narration(_payload_proyectado(6))
    assert texto.strip()
    assert "último período" in texto
    assert "9 períodos" in texto
    assert "9.0%" in texto
    assert "pasos 4, 5, 6" in texto   # extrapolados: margen heredado, no medido
    assert "pasos 1, 2, 3" in texto   # evidencia escasa: reliable=False
    assert "El total de los 6 pasos" in texto
    assert "los primeros 3 pasos" in texto
    assert "no es una promesa de precisión futura" in texto


def test_la_plantilla_nombra_el_grupo_con_mayor_proyeccion():
    """Sin LLM, la respuesta tiene que contestar "cual producto vende mas".

    El payload real no trae pasos por grupo: el numero que se cita es el total
    proyectado del grupo (`total.point`), tal cual viene. Un grupo sin total no
    se compara, y la frase se limita a los grupos PUBLICADOS (el top-N por
    volumen historico, no toda la dimension).
    """
    payload = _payload_proyectado(6)
    payload["breakdown"] = {
        "available": True,
        "reason": None,
        "dimension_column": "producto",
        "top_n": 3,
        "groups": [
            {"value": "Lácteos", "available": True, "total": {"point": 1500.0}},
            {"value": "Bebidas", "available": True, "total": {"point": 9000.0}},
            {"value": "Snacks", "available": False, "total": None},
        ],
    }
    texto = ml_service._template_narration(payload)
    assert "Entre los grupos publicados, el de mayor proyección es Bebidas" in texto
    assert "9.000" in texto
    assert "Snacks" not in texto


def test_sin_desglose_disponible_la_plantilla_no_inventa_un_grupo():
    payload = _payload_proyectado(6)
    sin_breakdown = ml_service._template_narration(payload)
    payload["breakdown"] = {
        "available": False,
        "reason": "La proyeccion principal no esta disponible; no se desglosa.",
        "groups": [{"value": "Lácteos", "available": True, "total": {"point": 1.0}}],
    }
    assert ml_service._template_narration(payload) == sin_breakdown
    assert "mayor proyección" not in sin_breakdown


def test_la_narrativa_sin_llm_publica_plantilla_nunca_vacia():
    payload = _payload_proyectado(6)
    for caida in (RuntimeError("apagado"), None, "   "):
        if isinstance(caida, Exception):
            mock = AsyncMock(side_effect=caida)
        else:
            mock = AsyncMock(return_value=caida)
        with patch(_LLM, new=mock):
            texto, fuente = asyncio.run(ml_service.narrate(payload))
        assert fuente == "plantilla", caida
        assert texto == ml_service._template_narration(payload)


def test_la_narrativa_que_contradice_el_horizonte_cae_a_plantilla():
    payload = _payload_proyectado(36)
    payload["horizon"] = 36
    payload["granularity"] = "month"
    for contradiccion in (
        "La proyección para los próximos 10 meses (hasta octubre de 2029) se sitúa en...",
        "La proyección total para el horizonte de 10 meses (enero de 2029 a octubre de 2029) es...",
        "Durante los 10 meses siguientes el total asciende a...",
    ):
        mock = AsyncMock(return_value=contradiccion)
        with patch(_LLM, new=mock):
            texto, fuente = asyncio.run(ml_service.narrate(payload))
        assert fuente == "plantilla", contradiccion
        assert texto == ml_service._template_narration(payload)


def test_la_narrativa_que_respeta_el_horizonte_se_publica():
    payload = _payload_proyectado(36)
    payload["horizon"] = 36
    payload["granularity"] = "month"
    mock = AsyncMock(return_value="La proyección para los próximos 36 meses se sitúa en...")
    with patch(_LLM, new=mock):
        texto, fuente = asyncio.run(ml_service.narrate(payload))
    assert fuente == "llm"
    assert "36 meses" in texto

    # El mismo horizonte en años es correcto: 36 meses = 3 años.
    mock = AsyncMock(return_value="La proyección para los próximos 3 años se sitúa en...")
    with patch(_LLM, new=mock):
        texto, fuente = asyncio.run(ml_service.narrate(payload))
    assert fuente == "llm"


def test_la_narrativa_con_jerga_cae_a_plantilla():
    payload = _payload_proyectado(36)
    payload["horizon"] = 36
    payload["granularity"] = "month"
    for jerga in (
        "El método naive fue el mejor para esta serie.",
        "La banda de error y los backtests muestran poca evidencia.",
        "El MAPE del paso es 2,5%.",
    ):
        mock = AsyncMock(return_value=jerga)
        with patch(_LLM, new=mock):
            texto, fuente = asyncio.run(ml_service.narrate(payload))
        assert fuente == "plantilla", jerga
        assert texto == ml_service._template_narration(payload)


def test_aviso_cuando_la_pregunta_pide_ganancia_con_porcentaje():
    note = ml_service._derived_metric_note(
        "Proyectame una ganancia con el 10% de las bases en 10 años",
        "monto_total",
    )
    assert note is not None
    assert "no aplica supuestos de porcentaje" in note
    assert "monto total" in note

    # Si la metrica elegida SI es de ganancia, no hay aviso.
    assert ml_service._derived_metric_note(
        "Proyectame una ganancia del 10% en 10 años", "margen_ganancia"
    ) is None
    # Sin porcentaje o sin palabra de ganancia, tampoco.
    assert ml_service._derived_metric_note(
        "proyecta las ventas del próximo año", "monto_total"
    ) is None
    assert ml_service._derived_metric_note(
        "proyecta la ganancia del próximo año", "monto_total"
    ) is None


def test_el_escenario_compone_el_crecimiento_anual():
    out = ml_service._scenario_projection(
        [100.0, 110.0, 121.0],
        ["2025-01", "2025-02", "2025-03"],
        12,
        "month",
        10.0,
    )
    assert out["available"] is True
    assert out["scenario"]["base_value"] == 121.0
    assert out["scenario"]["base_period"] == "2025-03"
    assert abs(out["scenario"]["final_value"] - 121.0 * 1.10) < 0.01
    assert abs(out["scenario"]["gain"] - (121.0 * 0.10)) < 0.01
    assert out["steps"][0]["period"] == "2025-04"
    assert out["steps"][-1]["period"] == "2026-03"
    assert all(
        step["lower"] is None and step["mape"] is None and step["n_backtests"] == 0
        for step in out["steps"]
    )
    assert out["quality"] is None

    # 120 meses = 10 años: composición anual, no lineal.
    long = ml_service._scenario_projection(
        [100.0, 110.0, 121.0],
        ["2025-01", "2025-02", "2025-03"],
        120,
        "month",
        10.0,
    )
    assert abs(long["scenario"]["final_value"] - 121.0 * (1.10 ** 10)) < 0.01
    assert len(long["steps"]) == 120


def test_la_narrativa_con_llm_publica_su_texto():
    with patch(_LLM, new=AsyncMock(return_value="Lectura en dos frases.")):
        assert asyncio.run(ml_service.narrate(_payload_proyectado(6))) == (
            "Lectura en dos frases.", "llm")


def test_el_prompt_de_la_narrativa_explica_los_campos_por_paso():
    # Sin la leyenda, el modelo confundio el `band_pct` de un paso con el `mape`
    # de otro (visto en vivo con Qwen2.5-Coder): los campos tienen que estar
    # nombrados en el prompt, no solo presentes en el JSON.
    prompt = ml_service._narration_prompt(_payload_proyectado(6))
    assert "`mape` es el error medido de ESE paso" in prompt
    assert "`extrapolated` marca banda heredada" in prompt
    # Visto en vivo dos veces con Qwen2.5-Coder: leyo 961.7 como "mayor" que
    # 1436.9 (es menor, y menor es mejor) y confundio n_backtests con historia.
    assert "Un MAPE mas bajo es mejor" in prompt
    assert "`n_backtests` cuenta los backtests de ESE paso" in prompt
    assert "`beats_naive: true` es ese veredicto ya calculado" in prompt
    # El desglose se cita solo si esta en el payload; el ranking es del total
    # proyectado de cada grupo, no de una suma de grupos.
    assert "si la pregunta pide un ranking" in prompt
    assert "mayor `total.point`" in prompt
    assert "no sumes los grupos" in prompt


# --- 5: la estimacion no mide, reusa lo medido --------------------------------

def test_estimate_sin_medicion_declara_fallback():
    from app.modules.catalog.services import enrich_estimator

    with patch.dict(enrich_estimator._SPEED_CACHE, {}, clear=True):
        assert ml_service.estimate_seconds("proyecta los ingresos", 6) == {
            "estimated_seconds": 60.0,
            "source": "fallback",
            "horizon_used": 6,
        }


def test_estimate_sin_horizonte_en_el_texto_usa_el_default():
    """Sin horizonte en la pregunta se estima con el default, no con el maximo.

    El router parsea la pregunta antes de llamar (`timeframe.parse_question_timeframe`);
    aca se prueba solo el fallback del servicio.
    """
    from app.modules.catalog.services import enrich_estimator

    with patch.dict(enrich_estimator._SPEED_CACHE, {}, clear=True):
        out = ml_service.estimate_seconds("proyecta los ingresos", None)
    assert out["source"] == "fallback"
    assert out["horizon_used"] == 6


def test_estimate_con_medicion_no_llama_al_llm():
    from app.modules.catalog.services import enrich_estimator
    from app.modules.chat_engine.llm_service import LLMService

    cache = {("", ""): (100.0, time.time())}
    with patch.dict(enrich_estimator._SPEED_CACHE, cache, clear=True), \
         patch.object(LLMService, "generate_completion",
                      new=AsyncMock(side_effect=AssertionError("la estimacion no puede llamar al LLM"))):
        out = ml_service.estimate_seconds("proyecta los ingresos", 6)

    assert out["source"] == "medido"
    assert out["horizon_used"] == 6
    assert 5.0 <= out["estimated_seconds"] < 60.0


def test_estimate_ignora_una_medicion_vencida():
    from app.modules.catalog.services import enrich_estimator

    vencida = {("", ""): (100.0, time.time() - enrich_estimator._SPEED_CACHE_TTL_S - 60)}
    with patch.dict(enrich_estimator._SPEED_CACHE, vencida, clear=True):
        assert ml_service.estimate_seconds("proyecta", 6)["source"] == "fallback"


# --- el borde del contrato ----------------------------------------------------

def test_horizonte_entre_1_y_36_o_auto():
    for valido in (1, 3, 6, 12, 18, 36):
        assert ProjectionRequest(question="proyecta", horizon=valido).horizon == valido
        assert ProjectionEstimateRequest(question="proyecta", horizon=valido).horizon == valido
    # `None` es Auto; el default del campo sigue siendo 6.
    assert ProjectionRequest(question="proyecta", horizon=None).horizon is None
    assert ProjectionEstimateRequest(question="proyecta", horizon=None).horizon is None
    assert ProjectionRequest(question="proyecta").horizon == 6
    assert ProjectionEstimateRequest(question="proyecta").horizon == 6
    for invalido in (0, -1, 37):
        with pytest.raises(ValidationError):
            ProjectionRequest(question="proyecta", horizon=invalido)
        with pytest.raises(ValidationError):
            ProjectionEstimateRequest(question="proyecta", horizon=invalido)

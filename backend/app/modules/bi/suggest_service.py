"""Sugerencias de visuales con la IA local.

La IA NO decide nada: propone definiciones y cada una se valida con el MISMO
`build_visual_sql` que usará el visual al ejecutarse. Una sugerencia que
propone una tabla, columna o agregación fuera del esquema autorizado del
usuario se descarta, no se "corrige". Nada se guarda ni se ejecuta aquí: el
usuario abre la sugerencia en el constructor, la previsualiza y decide.
"""

import json
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.modules.auth.models import User
from app.modules.bi import sql_builder
from app.modules.bi.governance import BIContext, BITableMeta, load_context, validate_table
from app.modules.bi.schemas import (
    BIFilter,
    BISuggestion,
    BISuggestionsOut,
    BIVisualSpec,
)
from app.modules.chat_engine.llm_service import LLMService

MAX_SUGGESTIONS = 4

_SUGGEST_SYSTEM = (
    "Eres un asistente de BI dentro de Dat.ia. Tu única tarea es proponer "
    "DEFINICIONES de gráficos para una tabla, en JSON. No consultas datos y "
    "no inventas columnas: usas SOLO las columnas de la lista del usuario. "
    "Respondes ÚNICAMENTE con un arreglo JSON válido, sin texto adicional y "
    "sin bloques de código."
)

# El prompt es tabla-acotado a propósito: menos tokens para el modelo local y
# ninguna tentación de proponer columnas de otras tablas.
_SUGGEST_PROMPT = """Tabla: {table}

Columnas (nombre: clase):
{columns}

Clases posibles: date, number, text.
Tipos de visual: bar, hbar, line, area, pie, donut, scatter, stacked, kpi, gauge, table.
Agregaciones: sum, avg, count, count_distinct, min, max.
Órdenes: value_desc, value_asc, label_asc, label_desc.
Granularidades de fecha (solo para dimensión date): day, month, year.

Reglas:
- Entre 2 y 4 sugerencias variadas: una tarjeta KPI, una evolución en el tiempo si hay columna date, un top-N por alguna categoría.
- "dimension" es obligatoria salvo para kpi y gauge; para line/area sobre una dimensión date usa date_grain "month".
- "measure" solo con columna de clase number y agregación sum o avg; para el resto usa count o count_distinct.
- "filters" solo si aportan algo, máximo 1, con columnas de la lista.
- "series" solo para stacked, con una columna distinta de "dimension".
- "title" breve y en español.

Responde con un arreglo JSON donde cada elemento tiene exactamente esta forma:
{{"title": "Ventas por categoría", "chart_type": "bar", "dimension": "una columna o null", "date_grain": null, "measure": "una columna o null", "aggregation": "count", "series": null, "filters": [], "sort": "value_desc", "limit": 20}}"""


def _parse_suggestions(text: str) -> Optional[List[Dict[str, Any]]]:
    """Extrae el arreglo JSON de la respuesta del LLM. None si no hay nada utilizable."""
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    start = cleaned.find("[")
    if start >= 0:
        end = cleaned.rfind("]")
        if end > start:
            try:
                parsed = json.loads(cleaned[start : end + 1])
            except Exception:
                return None
            return parsed if isinstance(parsed, list) else None
    # Un solo objeto también sirve: se normaliza a lista.
    start = cleaned.find("{")
    if start >= 0:
        end = cleaned.rfind("}")
        if end > start:
            try:
                parsed = json.loads(cleaned[start : end + 1])
            except Exception:
                return None
            return [parsed] if isinstance(parsed, dict) else None
    return None


def _coerce_spec(raw: Dict[str, Any], connection_id: int, table: str) -> BIVisualSpec:
    """Construye el spec desde el JSON del LLM. Lanza si algún literal no es válido.

    Pydantic rechaza aquí los enums inválidos (chart_type, op, sort, grain):
    la sugerencia se descarta en el caller y nunca llega al usuario.
    """
    raw_filters = raw.get("filters") or []
    if not isinstance(raw_filters, list):
        raw_filters = []
    return BIVisualSpec(
        connection_id=connection_id,
        table=table,
        chart_type=raw.get("chart_type") or "bar",
        dimension=raw.get("dimension") or None,
        date_grain=raw.get("date_grain") or None,
        measure=raw.get("measure") or None,
        aggregation=raw.get("aggregation") or "count",
        filters=[
            BIFilter(
                column=str(item.get("column") or ""),
                op=item.get("op") or "=",
                value=item.get("value", ""),
            )
            for item in raw_filters
            if isinstance(item, dict)
        ][:5],
        sort=raw.get("sort") or "value_desc",
        limit=int(raw.get("limit") or 20),
        series=raw.get("series") or None,
        size="small",
    )


def _runs_for_user(
    context: BIContext, table_meta: BITableMeta, spec: BIVisualSpec
) -> bool:
    """Validación de verdad: el spec debe construir el MISMO SQL que se ejecutaría.

    Un dry-run de `build_visual_sql` aplica todas las reglas de gobernanza
    (tabla permitida, columnas existentes, enmascaradas según rol, serie y
    filtros) sin tocar la base. Si falla, la sugerencia se descarta.
    """
    try:
        sql_builder.build_visual_sql(
            table=table_meta,
            spec=spec,
            blocked_columns=context.blocked_columns,
            is_admin=context.is_admin,
        )
        return True
    except Exception:
        return False


def _dedupe_key(spec: BIVisualSpec) -> tuple:
    filters_key = tuple(sorted((f.column, f.op, str(f.value)) for f in spec.filters))
    return (
        spec.chart_type,
        spec.dimension,
        spec.date_grain,
        spec.measure,
        spec.aggregation,
        spec.series,
        spec.sort,
        spec.limit,
        filters_key,
    )


async def suggest_visuals(
    *,
    db: Session,
    user: User,
    connection_id: int,
    table: str,
) -> BISuggestionsOut:
    context = load_context(db, user, connection_id, "Panel BI: sugerir visuales")
    table_meta = validate_table(context, table)
    if not table_meta.columns:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La tabla no tiene columnas visibles para tu perfil.",
        )

    columns = "\n".join(
        f"- {column.name}: {column.kind}" for column in table_meta.columns
    )
    prompt = _SUGGEST_PROMPT.format(table=table, columns=columns)

    try:
        text = await LLMService.generate_completion(
            prompt,
            system_prompt=_SUGGEST_SYSTEM,
            temperature=0.3,
            max_tokens=600,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"La IA local no está disponible para sugerir visuales: {exc}",
        )

    raw_items = _parse_suggestions(text or "")
    if raw_items is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La IA no devolvió sugerencias con un formato utilizable.",
        )

    suggestions: List[BISuggestion] = []
    seen = set()
    for raw in raw_items:
        if len(suggestions) >= MAX_SUGGESTIONS:
            break
        if not isinstance(raw, dict):
            continue
        try:
            spec = _coerce_spec(raw, connection_id, table)
        except Exception:
            continue
        key = _dedupe_key(spec)
        if key in seen:
            continue
        if not _runs_for_user(context, table_meta, spec):
            continue
        seen.add(key)
        title = str(raw.get("title") or "").strip()[:255]
        suggestions.append(
            BISuggestion(
                title=title or f"{spec.aggregation} en {table}",
                spec=spec,
            )
        )
    return BISuggestionsOut(suggestions=suggestions)

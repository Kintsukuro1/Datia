"""Resolvedor genérico de columnas para proyecciones.

Clasifica fecha, métrica, identificador y dimensión combinando, en este orden:

1. catálogo semántico (`SemanticCatalog.business_formula`);
2. perfil de datos (`SchemaInspector.profile_column`);
3. tipo físico de la columna.

El LLM no crea tipos ni expresiones: elige entre las opciones que produce este
módulo. Las expresiones SQL (`business_formula`, cast seguro, `COUNT(*)`) se
quedan del lado del backend; el cliente solo manda nombres de columna y `agg`.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.logging import logger
from app.modules.admin_catalog.models import CorporateConnection, SemanticCatalog
from app.modules.catalog.services.schema_inspector import (
    SchemaInspector,
    quote_sql_identifier,
)
from app.modules.chat_engine.ast_validator import ASTValidationError, ASTValidator
from app.modules.chat_engine.forecast_service import (
    _is_date_col,
    _is_dimension_col,
    _is_num_col,
)

# Tipos físicos de texto. Se usan solo para decidir si un perfil numérico puede
# convertirse en métrica con cast seguro.
_TEXT_TYPES = ("char", "text", "string", "enum")

# Numéricos con escala: una cantidad/importe, no un identificador por sí solo.
# Un ID suele ser entero (BIGINT/INT) o texto; un decimal con 100% de valores
# distintos sigue siendo una medición (el caso SAP `WRBTR NUMERIC`).
_DECIMAL_TYPES = ("numeric", "decimal", "money", "float", "double", "real")

# Nombres que, sin catálogo, hacen que una numérica sea una MÉTRICA y no un ID.
# La cardinalidad alta sola no alcanza: una serie corta puede tener un valor
# distinto por fila y seguir siendo una métrica (`valor`, `monto_total`).
_METRIC_NAME_TOKENS = (
    "monto", "total", "ingreso", "venta", "importe", "amount", "revenue",
    "value", "valor", "cantidad", "saldo", "precio", "costo", "gasto",
    "utilidad", "margen", "conteo", "count", "suma", "promedio", "peso",
)

# Nombres que identifican una fila o un documento, no una cantidad.
_IDENTIFIER_NAME_TOKENS = (
    "folio", "codigo", "cod_", "documento", "clave", "numero",
)

_NUMERIC_TEXT_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_NUMERIC_TEXT_SQL_RE = r"'^[+-]?[0-9]+(\.[0-9]+)?$'"

_DATE_FORMULA_RE = re.compile(
    r"\bTO_DATE\b|\bTO_TIMESTAMP\b|\bDATE\s*\(|::\s*date\b|\bAS\s+DATE\b",
    re.IGNORECASE,
)
_COUNT_DISTINCT_RE = re.compile(r"^\s*COUNT\s*\(\s*DISTINCT\b", re.IGNORECASE)
_AGG_FORMULA_RE = re.compile(r"^\s*(SUM|AVG|COUNT)\s*\(", re.IGNORECASE)
_NUMERIC_FORMULA_RE = re.compile(
    r"::\s*(numeric|decimal|integer|bigint|double|real)\b|"
    r"\bAS\s+(NUMERIC|DECIMAL|INTEGER|BIGINT|DOUBLE|REAL)\b",
    re.IGNORECASE,
)

# Orden de presentación: primero lo que tiene más evidencia.
_CONFIDENCE_ORDER = {"alta": 0, "media": 1, "baja": 2}


# ----------------------------------------------------------------------------
# Catálogo semántico
# ----------------------------------------------------------------------------

def formula_columns(formula: str) -> Optional[Set[str]]:
    """Columnas que nombra una fórmula, o None si no parsea.

    Se usa el mismo parser que el `ASTValidator`; una fórmula que no parsea no
    se ofrece. No alcanza con buscar identificadores con regex: las funciones y
    los literales se confundirían con columnas.
    """
    try:
        from sqlglot import exp, parse_one

        expression = parse_one(f"SELECT {formula} AS v", dialect="postgres")
    except Exception:
        return None
    return {
        column.name.lower()
        for column in expression.find_all(exp.Column)
        if column.name
    }


def formula_is_safe(
    formula: str,
    table: str,
    candidate_names: Set[str],
    allowed_tables: Set[str],
    blocked_columns: Set[str],
) -> bool:
    """La fórmula pasa el AST y solo usa columnas que el rol puede leer.

    El `blocked_columns` del validador no alcanza para MASKED: una métrica
    enmascarada no se suma. Por eso se exige que TODAS las columnas de la
    fórmula estén en `candidate_names` (que ya excluye BLOCKED y MASKED).
    """
    columns = formula_columns(formula)
    if columns is None or not columns <= candidate_names:
        return False
    sql = (
        f"SELECT {formula} AS v FROM {quote_sql_identifier(table)} LIMIT 1"
    )
    try:
        ASTValidator.validate_and_secure_sql(
            sql,
            dialect="postgres",
            allowed_tables=allowed_tables or {table},
            blocked_columns=blocked_columns or set(),
            is_admin=False,
        )
    except (ASTValidationError, ValueError):
        return False
    return True


def classify_formula_kind(formula: Optional[str]) -> Optional[str]:
    """Qué representa una fórmula de catálogo, o None si no se reconoce.

    - `date`: produce una fecha.
    - `count_distinct`: ya es un conteo de valores distintos.
    - `aggregate`: ya es una agregación (SUM/AVG/COUNT); no se vuelve a envolver.
    - `numeric`: expresión escalar numérica.
    """
    if not formula:
        return None
    if _DATE_FORMULA_RE.search(formula):
        return "date"
    if _COUNT_DISTINCT_RE.match(formula):
        return "count_distinct"
    if _AGG_FORMULA_RE.match(formula):
        return "aggregate"
    if _NUMERIC_FORMULA_RE.search(formula):
        return "numeric"
    return None


def _is_self_sum(formula: str, name: str) -> bool:
    """`SUM(<la misma columna>)`: una suma escalar, no una fórmula pre-agregada.

    Tratarla como escalar conserva el filtro `> 0` que ya usa la proyección y
    evita que un `SUM(monto)` de catálogo cambie de significado solo por existir.
    """
    quoted = re.escape(quote_sql_identifier(name))
    bare = re.escape(name)
    pattern = rf"^\s*SUM\s*\(\s*(?:{quoted}|{bare})\s*\)\s*$"
    return bool(re.match(pattern, formula, re.IGNORECASE))


def load_catalog_entries(
    db: Session,
    connection_id: int,
    table: str,
    candidate_names: Set[str],
    allowed_tables: Set[str],
    blocked_columns: Set[str],
) -> Dict[str, Dict[str, Any]]:
    """Entradas del catálogo para la tabla, con las fórmulas ya validadas.

    Una fórmula inválida no se descarta del todo: la entrada sigue aportando
    `friendly_name`/`description` como etiqueta, pero sin fórmula. Así el
    resolvedor puede caer al perfil o al tipo físico sin perder el nombre de
    negocio.
    """
    try:
        rows = (
            db.query(SemanticCatalog)
            .filter(
                SemanticCatalog.connection_id == connection_id,
                func.lower(SemanticCatalog.table_name) == table.lower(),
            )
            .all()
        )
    except Exception as exc:
        logger.warning("Catalogo semantico no disponible para %s: %s", table, exc)
        return {}

    entries: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        column = str(row.column_name or "").strip()
        if not column:
            # La entrada de tabla (column_name NULL) la usa el matcher de tabla;
            # no clasifica columnas.
            continue
        formula = str(row.business_formula or "").strip() or None
        if formula and classify_formula_kind(formula) is None:
            formula = None
        if formula and not formula_is_safe(
            formula, table, candidate_names, allowed_tables, blocked_columns
        ):
            logger.warning(
                "Formula de catalogo descartada para %s.%s", table, column
            )
            formula = None
        entries[column.lower()] = {
            "column": column,
            "friendly_name": row.friendly_name,
            "description": row.description,
            "synonyms": row.synonyms,
            "business_formula": formula,
        }
    return entries


# ----------------------------------------------------------------------------
# Selección de tabla: nombre, catálogo y pregunta
# ----------------------------------------------------------------------------

# Prefijos técnicos que no describen el negocio de la tabla.
_TABLE_PREFIXES = {"fact", "fct", "dim", "dwh", "table", "tabla"}

# Palabras de la pregunta que no ayudan a elegir tabla.
_TABLE_STOPWORDS = {
    "proyecta", "proyectar", "proyeccion", "proyecciones", "estima", "estimar",
    "calcula", "calcular", "dame", "dime", "muestra", "mostrar", "analiza",
    "analizar", "cuanto", "cuantos", "cuanta", "cuantas", "para", "por",
    "con", "sin", "sobre", "entre", "desde", "hasta", "como", "que", "cual",
    "cuales", "los", "las", "del", "una", "uno", "unos", "unas", "este",
    "esta", "estos", "estas", "todos", "todas", "tiene", "tienen", "ver",
    "the", "and", "for", "with", "from", "show", "get", "give",
}


def _normalize_text(text: Any) -> str:
    """Minúsculas sin acentos, con separadores colapsados a espacios."""
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", ascii_text).strip()


def _table_tokens(text: str) -> Set[str]:
    return {
        token for token in _normalize_text(text).split()
        if len(token) >= 3
        and token not in _TABLE_STOPWORDS
        and token not in _TABLE_PREFIXES
    }


def table_catalog(db: Session, connection_id: int) -> Dict[str, Dict[str, Any]]:
    """Entradas de tabla (`column_name IS NULL`) por nombre en minúsculas."""
    try:
        rows = (
            db.query(SemanticCatalog)
            .filter(
                SemanticCatalog.connection_id == connection_id,
                SemanticCatalog.column_name.is_(None),
            )
            .all()
        )
    except Exception as exc:
        logger.warning("Catalogo de tablas no disponible: %s", exc)
        return {}
    return {
        str(row.table_name).lower(): {
            "friendly_name": row.friendly_name,
            "description": row.description,
            "synonyms": row.synonyms,
        }
        for row in rows
    }


def _table_score(
    question: str,
    question_tokens: Set[str],
    table_name: str,
    entry: Dict[str, Any],
) -> int:
    name_tokens = _table_tokens(table_name.replace("_", " "))
    catalog_text = " ".join(
        str(entry.get(field) or "")
        for field in ("friendly_name", "synonyms", "description")
    )
    catalog_tokens = _table_tokens(catalog_text)
    normalized_question = f" {_normalize_text(question)} "

    score = 0
    name_phrase = _normalize_text(table_name.replace("_", " "))
    if name_phrase and f" {name_phrase} " in normalized_question:
        score += 100
    friendly_phrase = _normalize_text(str(entry.get("friendly_name") or ""))
    if friendly_phrase and f" {friendly_phrase} " in normalized_question:
        score += 90
    score += 20 * len(name_tokens & question_tokens)
    score += 15 * len((catalog_tokens - name_tokens) & question_tokens)
    return score


def _is_dimension_table(table_name: str) -> bool:
    lowered = str(table_name).lower()
    return lowered.startswith("dim") or lowered.startswith("dwh_dim")


def pick_table(
    question: Optional[str],
    tables: Sequence[Dict[str, Any]],
    catalog: Dict[str, Dict[str, Any]],
) -> Tuple[Optional[str], str]:
    """Tabla por pregunta y catálogo; si no hay match, la de más filas.

    Devuelve `(nombre, origen)` con origen `"catalogo"` o `"mayor"`. El empate
    se resuelve primero a favor de la tabla de hechos (no `dim_*`), después por
    filas y por nombre, para que la misma pregunta siempre elija la misma tabla.
    """
    if not tables:
        return None, "mayor"
    question_tokens = _table_tokens(question or "")
    scored = [
        (
            _table_score(
                question or "",
                question_tokens,
                str(item["name"]),
                catalog.get(str(item["name"]).lower()) or {},
            ),
            int(item.get("row_count") or 0),
            str(item["name"]),
        )
        for item in tables
    ]
    best_score = max(score for score, _, _ in scored)
    if best_score <= 0:
        largest = max(tables, key=lambda item: int(item.get("row_count") or 0))
        return str(largest["name"]), "mayor"
    candidates = [item for item in scored if item[0] == best_score]
    candidates.sort(
        key=lambda item: (
            _is_dimension_table(item[2]),
            -item[1],
            item[2],
        )
    )
    return candidates[0][2], "catalogo"


def public_tables(
    tables: Sequence[Dict[str, Any]],
    catalog: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Lista de tablas para el selector, con el nombre de negocio si existe."""
    result = []
    for item in tables:
        name = str(item["name"])
        entry = catalog.get(name.lower()) or {}
        result.append({
            "name": name,
            "row_count": int(item.get("row_count") or 0),
            "friendly_name": entry.get("friendly_name"),
        })
    return result


# ----------------------------------------------------------------------------
# Perfil de datos
# ----------------------------------------------------------------------------

def profile_columns(
    connection: CorporateConnection,
    table: str,
    cols: Sequence[Dict[str, Any]],
    row_count: int,
) -> Dict[str, Dict[str, Any]]:
    """Perfil de las columnas que el catálogo o el tipo físico no resolvieron.

    Se reutiliza `SchemaInspector.profile_column`: no hay un segundo perfilador.
    Un fallo de conexión degrada a "sin perfil" y la clasificación sigue con
    catálogo + tipo físico; la proyección no se cae por no poder perfilar.
    """
    if not cols:
        return {}

    from app.core.database import connector_engine

    table_ref = quote_sql_identifier(table)
    profiles: Dict[str, Dict[str, Any]] = {}
    try:
        with connector_engine(connection) as engine, engine.connect() as conn:
            for col in cols:
                name = str(col.get("name") or "").strip()
                if not name:
                    continue
                profiles[name.lower()] = SchemaInspector.profile_column(
                    conn,
                    table_ref,
                    name,
                    str(col.get("type") or ""),
                    row_count,
                    include_values=True,
                )
    except Exception as exc:
        logger.warning("Perfil de columnas no disponible para %s: %s", table, exc)
        return {}
    return profiles


def _profile_ok(profile: Optional[Dict[str, Any]]) -> bool:
    return bool(profile) and not profile.get("error") and "distintos" in profile


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _looks_like_metric_name(name: str) -> bool:
    lowered = str(name).lower()
    return any(token in lowered for token in _METRIC_NAME_TOKENS)


def _looks_like_identifier_name(name: str) -> bool:
    lowered = str(name).lower()
    return (
        lowered == "id"
        or lowered.startswith("id_")
        or lowered.endswith("_id")
        or any(token in lowered for token in _IDENTIFIER_NAME_TOKENS)
    )


def _is_text_type(col_type: str) -> bool:
    return any(token in str(col_type).lower() for token in _TEXT_TYPES)


def _is_decimal_type(col_type: str) -> bool:
    return any(token in str(col_type).lower() for token in _DECIMAL_TYPES)


def _numeric_ratio(profile: Dict[str, Any]) -> float:
    values: List[Any] = []
    for item in profile.get("top") or []:
        if isinstance(item, dict):
            values.append(item.get("valor"))
    values.extend(profile.get("muestras") or [])
    values = [v for v in values if v is not None and str(v).strip()]
    if not values:
        return 0.0
    numeric = sum(1 for value in values if _NUMERIC_TEXT_RE.match(str(value).strip()))
    return numeric / len(values)


def _parse_iso(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (TypeError, ValueError):
        return None


def _parse_ddmmyyyy(value: Any) -> Optional[date]:
    text = str(value).strip()
    if not text.isdigit() or len(text) not in (7, 8):
        return None
    text = text.zfill(8)
    try:
        return date(int(text[4:]), int(text[2:4]), int(text[:2]))
    except ValueError:
        return None


def _parse_slash(value: Any, *, ymd: bool) -> Optional[date]:
    parts = str(value).strip().split("/")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    try:
        if ymd:
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
        return date(int(parts[2]), int(parts[1]), int(parts[0]))
    except ValueError:
        return None


def _valid_range(first: Optional[date], last: Optional[date]) -> bool:
    return bool(
        first and last
        and first <= last
        and 1900 <= first.year <= 2100
        and 1900 <= last.year <= 2100
    )


def _date_expression(
    name: str, col: Dict[str, Any], profile: Dict[str, Any]
) -> Optional[Tuple[str, str]]:
    """Expresión SQL de fecha detectada por perfil, o None.

    Devuelve `(expresión, patrón)`. La expresión siempre está guardada con un
    `CASE`/regex para que un valor suelto fuera de patrón no reviente la query.
    """
    pattern = str(profile.get("patron") or "")
    first_raw, last_raw = profile.get("min"), profile.get("max")
    if not pattern or first_raw is None or last_raw is None:
        return None

    quoted = quote_sql_identifier(name)
    text = f"TRIM(CAST({quoted} AS TEXT))"

    if "fecha-hora ISO" in pattern:
        first, last = _parse_iso(first_raw), _parse_iso(last_raw)
        if _valid_range(first, last):
            return (
                f"CASE WHEN {text} ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}[ T]' "
                f"THEN CAST({text} AS TIMESTAMP) END",
                pattern,
            )
    if "fecha ISO" in pattern or "YYYY-MM-DD" in pattern:
        first, last = _parse_iso(first_raw), _parse_iso(last_raw)
        if _valid_range(first, last):
            return (
                f"CASE WHEN {text} ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}' "
                f"THEN CAST({text} AS DATE) END",
                pattern,
            )
    if "DDMMYYYY" in pattern:
        first, last = _parse_ddmmyyyy(first_raw), _parse_ddmmyyyy(last_raw)
        if _valid_range(first, last):
            return (
                f"CASE WHEN {text} ~ '^\\d{{7,8}}$' "
                f"THEN TO_DATE(LPAD({text}, 8, '0'), 'DDMMYYYY') END",
                pattern,
            )
    if "DD/MM/YYYY" in pattern:
        first, last = _parse_slash(first_raw, ymd=False), _parse_slash(last_raw, ymd=False)
        if _valid_range(first, last):
            return (
                f"CASE WHEN {text} ~ '^\\d{{2}}/\\d{{2}}/\\d{{4}}$' "
                f"THEN TO_DATE({text}, 'DD/MM/YYYY') END",
                pattern,
            )
    if "YYYY/MM/DD" in pattern:
        first, last = _parse_slash(first_raw, ymd=True), _parse_slash(last_raw, ymd=True)
        if _valid_range(first, last):
            return (
                f"CASE WHEN {text} ~ '^\\d{{4}}/\\d{{2}}/\\d{{2}}$' "
                f"THEN TO_DATE({text}, 'YYYY/MM/DD') END",
                pattern,
            )
    return None


def _numeric_cast(name: str) -> str:
    quoted = quote_sql_identifier(name)
    text = f"TRIM(CAST({quoted} AS TEXT))"
    return (
        f"CASE WHEN {text} ~ {_NUMERIC_TEXT_SQL_RE} "
        f"THEN CAST({text} AS NUMERIC) END"
    )


def _identifier_evidence(
    name: str, col: Dict[str, Any], distinct: int, row_count: int
) -> bool:
    """Si la columna identifica filas/documentos, no una cantidad.

    Una métrica con nombre reconocible (`monto`, `valor`, `cantidad`) NUNCA se
    convierte en identificador por cardinalidad: una tabla de 9 filas puede
    tener 9 valores distintos y seguir siendo una serie de montos.
    """
    if bool(col.get("is_pk")) or _looks_like_identifier_name(name):
        return True
    if _looks_like_metric_name(name):
        return False
    # Un decimal sin nombre de identificador es una cantidad, aunque cada fila
    # tenga un valor distinto. El ID suele ser entero o texto.
    if _is_decimal_type(str(col.get("type") or "")):
        return False
    return row_count > 0 and (distinct / row_count) >= 0.95


# ----------------------------------------------------------------------------
# Clasificación
# ----------------------------------------------------------------------------

def _option(
    column: str,
    kind: str,
    label: str,
    source: str,
    confidence: str,
    evidence: str,
    *,
    agg: Optional[str] = None,
    expression: Optional[str] = None,
    sql_agg: Optional[str] = None,
    min_value: Optional[str] = None,
    max_value: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "kind": kind,
        "column": column,
        "label": label,
        "source": source,
        "confidence": confidence,
        "evidence": evidence,
        "agg": agg,
        "expression": expression,
        "sql_agg": sql_agg,
        "min": min_value,
        "max": max_value,
    }


def _catalog_option(
    name: str,
    label: str,
    col: Dict[str, Any],
    entry: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    formula = entry.get("business_formula")
    kind = classify_formula_kind(formula)
    if kind is None:
        return None
    # Una columna DATE/TIMESTAMP no es una métrica: si el catálogo tiene un
    # SUM/COUNT sobre una fecha, la fórmula es inválida para esa columna y gana
    # el tipo físico. El catálogo se revisa, pero no se le cree a ciegas.
    if _is_date_col(col) and kind != "date":
        return None
    # `DATE(col)` sobre una numérica sin cast (típico `DATE(anio)` generado por
    # IA) no es una fecha válida en Postgres. Se exige una conversión explícita.
    if (
        kind == "date"
        and _is_num_col(col)
        and not re.search(
            r"TO_DATE|TO_TIMESTAMP|CAST|::\s*date|\bAS\s+DATE\b",
            formula,
            re.IGNORECASE,
        )
    ):
        return None
    quoted = quote_sql_identifier(name)
    if kind == "date":
        return _option(
            name, "date", label, "catalogo", "alta",
            "fórmula de fecha del catálogo", expression=formula,
        )
    if kind == "count_distinct":
        return _option(
            name, "identifier", label, "catalogo", "alta",
            "fórmula COUNT(DISTINCT) del catálogo",
            agg="count_distinct", expression=formula, sql_agg="formula",
        )
    if kind == "aggregate" and _is_self_sum(formula, name) and _is_num_col(col):
        # `SUM(col)` sobre la MISMA columna es una suma escalar: no se
        # pre-agrega dos veces y conserva el filtro de ingresos.
        return _option(
            name, "metric", label, "catalogo", "alta",
            "suma del catálogo sobre la misma columna",
            agg="sum", expression=quoted, sql_agg="sum",
        )
    if kind == "aggregate":
        return _option(
            name, "metric", label, "catalogo", "alta",
            "fórmula agregada del catálogo",
            agg="formula", expression=formula, sql_agg="formula",
        )
    if kind == "numeric" or (formula and _is_num_col(col)):
        return _option(
            name, "metric", label, "catalogo", "alta",
            "fórmula numérica del catálogo",
            agg="sum", expression=formula or quoted, sql_agg="sum",
        )
    return None


def _profile_option(
    name: str,
    label: str,
    col: Dict[str, Any],
    profile: Dict[str, Any],
    row_count: int,
) -> Optional[Dict[str, Any]]:
    col_type = str(col.get("type") or "")
    distinct = _int(profile.get("distintos"))
    ratio = (distinct / row_count) if row_count > 0 else 0.0
    quoted = quote_sql_identifier(name)

    date_expression = _date_expression(name, col, profile)
    if date_expression is not None:
        expression, pattern = date_expression
        return _option(
            name, "date", label, "perfil", "media",
            f"patrón {pattern} y rango {profile.get('min')}–{profile.get('max')}",
            expression=expression,
            min_value=profile.get("min"), max_value=profile.get("max"),
        )

    if _identifier_evidence(name, col, distinct, row_count):
        evidence = (
            f"{distinct} valores distintos sobre {row_count} filas"
            if row_count and distinct / row_count >= 0.95
            else "tipo y nombre de identificador"
        )
        return _option(
            name, "identifier", label, "perfil", "media",
            evidence, agg="count_distinct", expression=quoted,
            sql_agg="count_distinct",
        )

    if _looks_like_metric_name(name):
        if _is_num_col(col):
            return _option(
                name, "metric", label, "perfil", "media",
                f"tipo {col_type}", agg="sum", expression=quoted, sql_agg="sum",
            )
        if _is_text_type(col_type) and _numeric_ratio(profile) >= 0.95:
            return _option(
                name, "metric", label, "perfil", "media",
                "mayoría de las muestras son numéricas",
                agg="sum", expression=_numeric_cast(name), sql_agg="sum",
            )

    if _is_num_col(col) and distinct <= 100 and ratio < 0.01:
        return _option(
            name, "metric", label, "perfil", "baja",
            f"{distinct} valores distintos; puede ser un código, no una cantidad",
            agg="sum", expression=quoted, sql_agg="sum",
        )

    if _is_num_col(col):
        return _option(
            name, "metric", label, "perfil", "media",
            f"tipo {col_type}", agg="sum", expression=quoted, sql_agg="sum",
        )

    if _is_text_type(col_type) and _numeric_ratio(profile) >= 0.95:
        return _option(
            name, "metric", label, "perfil", "media",
            "mayoría de las muestras son numéricas",
            agg="sum", expression=_numeric_cast(name), sql_agg="sum",
        )

    if _is_dimension_col(col):
        return _option(
            name, "dimension", label, "perfil", "media",
            f"{distinct} valores distintos",
        )
    return None


def _physical_option(
    name: str, label: str, col: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    quoted = quote_sql_identifier(name)
    col_type = str(col.get("type") or "")
    if _is_date_col(col):
        return _option(
            name, "date", label, "tipo", "alta",
            f"tipo {col_type}", expression=quoted,
        )
    if _is_num_col(col):
        if _identifier_evidence(name, col, 0, 0):
            return _option(
                name, "identifier", label, "tipo", "media",
                "tipo y nombre de identificador",
                agg="count_distinct", expression=quoted, sql_agg="count_distinct",
            )
        return _option(
            name, "metric", label, "tipo", "media",
            f"tipo {col_type}", agg="sum", expression=quoted, sql_agg="sum",
        )
    if _looks_like_identifier_name(name):
        return _option(
            name, "identifier", label, "tipo", "media",
            "nombre de identificador",
            agg="count_distinct", expression=quoted, sql_agg="count_distinct",
        )
    if _is_dimension_col(col):
        return _option(
            name, "dimension", label, "tipo", "media", f"tipo {col_type}",
        )
    return None


def _add_option(
    option: Dict[str, Any],
    date_options: List[Dict[str, Any]],
    metric_options: List[Dict[str, Any]],
    identifier_options: List[Dict[str, Any]],
    dimension_columns: List[str],
) -> None:
    kind = option["kind"]
    if kind == "date":
        date_options.append(option)
    elif kind == "metric":
        metric_options.append(option)
    elif kind == "identifier":
        identifier_options.append(option)
    elif kind == "dimension":
        dimension_columns.append(str(option["column"]))


def _count_option() -> Dict[str, Any]:
    return _option(
        "*", "metric", "Cantidad de registros", "sintetica", "alta",
        "cuenta filas por periodo", agg="count",
    )


def resolve_columns(
    cols: Sequence[Dict[str, Any]],
    catalog: Dict[str, Dict[str, Any]],
    profiles: Dict[str, Dict[str, Any]],
    row_count: int,
) -> Dict[str, Any]:
    """Clasifica columnas sin tocar la base.

    Entra metadatos reales; sale una lista de opciones con su evidencia. Las
    claves `expression`/`sql_agg` son internas y no se publican por HTTP.
    """
    date_options: List[Dict[str, Any]] = []
    metric_options: List[Dict[str, Any]] = []
    identifier_options: List[Dict[str, Any]] = []
    dimension_columns: List[str] = []
    reviewed: List[str] = []

    for col in cols:
        name = str(col.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        entry = catalog.get(key) or {}
        label = str(entry.get("friendly_name") or name)

        option = _catalog_option(name, label, col, entry)
        if option is None and not _is_date_col(col):
            profile = profiles.get(key)
            if _profile_ok(profile):
                option = _profile_option(name, label, col, profile, row_count)
        if option is None:
            option = _physical_option(name, label, col)
        if option is None:
            reviewed.append(name)
            continue
        _add_option(
            option, date_options, metric_options, identifier_options,
            dimension_columns,
        )

    for collection in (date_options, metric_options, identifier_options):
        collection.sort(key=lambda item: _CONFIDENCE_ORDER.get(item["confidence"], 9))

    return {
        "date_options": date_options,
        "metric_options": metric_options,
        "identifier_options": identifier_options,
        "dimension_columns": dimension_columns,
        "count_option": _count_option() if date_options else None,
        "reviewed": reviewed,
    }


def public_option(option: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Opción sin las claves internas (`expression`, `sql_agg`)."""
    if not option:
        return None
    keys = (
        "column", "label", "source", "confidence", "evidence",
        "agg", "min", "max",
    )
    return {
        key: option[key]
        for key in keys
        if key in option and option[key] is not None
    }


def find_option(
    options: Sequence[Dict[str, Any]], value: Any
) -> Optional[Dict[str, Any]]:
    """Opción de la lista cuyo nombre coincide con `value`, sin distinguir caso."""
    if value is None:
        return None
    target = str(value).strip().lower()
    if target == "*":
        return next(
            (option for option in options if option.get("column") == "*"), None
        )
    for option in options:
        if str(option.get("column", "")).lower() == target:
            return option
    return None


def build_resolution(
    db: Session,
    connection: CorporateConnection,
    table: str,
    candidate_cols: Sequence[Dict[str, Any]],
    row_count: int,
    allowed_tables: Optional[Set[str]],
    blocked_columns: Optional[Set[str]],
    budget: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolución completa para la tabla, cacheada en el budget de la request."""
    cache_key = f"resolution:{table.lower()}"
    if budget is not None and cache_key in budget:
        return budget[cache_key]

    candidate_names = {
        str(col.get("name") or "").lower()
        for col in candidate_cols
        if col.get("name")
    }
    catalog = load_catalog_entries(
        db, connection.id, table, candidate_names,
        set(allowed_tables or {table}), set(blocked_columns or set()),
    )

    to_profile: List[Dict[str, Any]] = []
    for col in candidate_cols:
        name = str(col.get("name") or "").strip()
        if not name:
            continue
        entry = catalog.get(name.lower()) or {}
        label = str(entry.get("friendly_name") or name)
        # Solo se salta el perfil si la fórmula del catálogo se va a usar de
        # verdad. Una fórmula reconocida pero incompatible con el tipo físico
        # (`SUM(fecha)`, `DATE(anio)`) no resuelve la columna.
        if _catalog_option(name, label, col, entry) is not None:
            continue
        if _is_date_col(col):
            continue
        to_profile.append(col)

    profiles = profile_columns(connection, table, to_profile, row_count)
    resolution = resolve_columns(candidate_cols, catalog, profiles, row_count)
    if budget is not None:
        budget[cache_key] = resolution
    return resolution

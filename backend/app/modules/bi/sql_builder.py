"""Construcción de SQL del módulo BI.

Reglas:
- Solo PostgreSQL.
- Los identificadores salen del esquema autorizado, no del request.
- Los valores se escapan con `sqlglot` y nunca se interpolan a mano.
- El SQL resultante SIEMPRE pasa por `ASTValidator` antes de ejecutarse.
"""

import math
import re
from typing import List, Optional, Sequence, Tuple

from fastapi import HTTPException, status
from sqlglot import exp

from app.modules.catalog.services.schema_inspector import quote_sql_identifier
from app.modules.bi.governance import BIAccessDenied, BIColumnMeta, BITableMeta
from app.modules.bi.schemas import BIFilter, BIVisualSpec

_INTEGER_RE = re.compile(r"^-?\d+$")


def ident(name: str) -> str:
    """Cita un identificador físico (tabla o columna)."""
    return quote_sql_identifier(name)


def sql_literal(value: object, kind: str = "text") -> str:
    """Devuelve un literal SQL seguro para un valor del request.

    El tipo de la columna decide el formato: una columna de texto conserva
    ceros a la izquierda; una numérica se envía como número cuando el valor es
    parseable. Si no, se manda como texto y PostgreSQL decide.
    """
    if value is None:
        return "NULL"
    if kind == "number" and not isinstance(value, bool):
        text = str(value).strip()
        if _INTEGER_RE.match(text):
            return text
        try:
            number = float(text)
        except (TypeError, ValueError):
            pass
        else:
            if math.isfinite(number):
                return exp.Literal.number(number).sql(dialect="postgres")
    return exp.Literal.string(str(value)).sql(dialect="postgres")


def date_part_expr(column_sql: str, part: str) -> str:
    """Expresión de año/mes sobre una columna de fecha."""
    if part == "year":
        return f"EXTRACT(YEAR FROM CAST({column_sql} AS DATE))"
    if part == "month":
        return f"EXTRACT(MONTH FROM CAST({column_sql} AS DATE))"
    if part == "day":
        return f"TO_CHAR(CAST({column_sql} AS DATE), 'YYYY-MM-DD')"
    raise ValueError(f"Período no soportado: {part}")


def date_bucket_expr(column_sql: str, grain: str) -> str:
    """Expresión de agrupación temporal para el constructor de visuales."""
    if grain == "day":
        return f"TO_CHAR(CAST({column_sql} AS DATE), 'YYYY-MM-DD')"
    if grain == "month":
        return f"TO_CHAR(CAST({column_sql} AS DATE), 'YYYY-MM')"
    if grain == "year":
        return f"CAST(EXTRACT(YEAR FROM CAST({column_sql} AS DATE)) AS INTEGER)"
    raise ValueError(f"Gránulo no soportado: {grain}")


def measure_expr(measure: Optional[str], aggregation: str = "sum") -> str:
    """Expresión de la medida. `measure=None` o `count` => COUNT(*)."""
    if not measure or measure.lower() == "count":
        return "COUNT(*)"
    column_sql = ident(measure)
    if aggregation == "count":
        return f"COUNT({column_sql})"
    if aggregation == "count_distinct":
        return f"COUNT(DISTINCT {column_sql})"
    if aggregation == "avg":
        return f"AVG({column_sql})"
    if aggregation == "min":
        return f"MIN({column_sql})"
    if aggregation == "max":
        return f"MAX({column_sql})"
    return f"SUM({column_sql})"


def build_cardinality_sql(
    table: BITableMeta, columns: Sequence[BIColumnMeta]
) -> Optional[str]:
    if not columns:
        return None
    parts = [
        f"COUNT(DISTINCT {ident(column.name)}) AS c{index}"
        for index, column in enumerate(columns)
    ]
    return f"SELECT {', '.join(parts)} FROM {ident(table.name)}"


def build_years_sql(table: BITableMeta, date_column: str) -> str:
    column_sql = ident(date_column)
    return (
        f"SELECT DISTINCT {date_part_expr(column_sql, 'year')} AS y "
        f"FROM {ident(table.name)} "
        f"WHERE {column_sql} IS NOT NULL ORDER BY 1 DESC"
    )


def build_months_sql(table: BITableMeta, date_column: str, year: int) -> str:
    column_sql = ident(date_column)
    return (
        f"SELECT DISTINCT {date_part_expr(column_sql, 'month')} AS m "
        f"FROM {ident(table.name)} "
        f"WHERE {date_part_expr(column_sql, 'year')} = {int(year)} ORDER BY 1"
    )


def build_where(
    *,
    date_column: Optional[str],
    group_column: Optional[str],
    category_column: Optional[str],
    year: Optional[int],
    month: Optional[int],
    group_value: Optional[str],
    category_value: Optional[str],
) -> str:
    conditions: List[str] = []
    if date_column and year is not None:
        conditions.append(
            f"{date_part_expr(ident(date_column), 'year')} = {int(year)}"
        )
    if date_column and month is not None:
        conditions.append(
            f"{date_part_expr(ident(date_column), 'month')} = {int(month)}"
        )
    if group_column and group_value not in (None, ""):
        conditions.append(
            f"CAST({ident(group_column)} AS TEXT) = {sql_literal(group_value, 'text')}"
        )
    if category_column and category_value not in (None, ""):
        conditions.append(
            f"CAST({ident(category_column)} AS TEXT) = {sql_literal(category_value, 'text')}"
        )
    return f" WHERE {' AND '.join(conditions)}" if conditions else ""


def _with_date_not_null(where: str, date_column: str) -> str:
    condition = f"{ident(date_column)} IS NOT NULL"
    return f"{where} AND {condition}" if where else f" WHERE {condition}"


def build_kpis_sql(
    *,
    table: BITableMeta,
    where: str,
    measure: Optional[str],
    group_column: Optional[str],
    date_column: Optional[str],
) -> str:
    parts = ["COUNT(*) AS n", f"{measure_expr(measure)} AS total"]
    if group_column:
        parts.append(f"COUNT(DISTINCT {ident(group_column)}) AS grupos")
    if date_column:
        parts.append(
            f"COUNT(DISTINCT {date_part_expr(ident(date_column), 'day')}) AS dias"
        )
    return f"SELECT {', '.join(parts)} FROM {ident(table.name)}{where}"


def build_group_sql(
    *,
    table: BITableMeta,
    where: str,
    measure: Optional[str],
    group_column: str,
    limit: int = 25,
) -> str:
    label_sql = f"CAST({ident(group_column)} AS TEXT)"
    return (
        f"SELECT {label_sql} AS label, {measure_expr(measure)} AS value "
        f"FROM {ident(table.name)}{where} "
        f"GROUP BY {label_sql} ORDER BY value DESC LIMIT {int(limit)}"
    )


def build_category_sql(
    *,
    table: BITableMeta,
    where: str,
    measure: Optional[str],
    category_column: str,
    limit: int = 12,
) -> str:
    label_sql = f"CAST({ident(category_column)} AS TEXT)"
    return (
        f"SELECT {label_sql} AS label, {measure_expr(measure)} AS value "
        f"FROM {ident(table.name)}{where} "
        f"GROUP BY {label_sql} ORDER BY value DESC LIMIT {int(limit)}"
    )


def build_daily_sql(
    *,
    table: BITableMeta,
    where: str,
    measure: Optional[str],
    date_column: str,
    month: Optional[int],
) -> str:
    bucket = date_bucket_expr(ident(date_column), "day" if month else "month")
    scoped_where = _with_date_not_null(where, date_column)
    return (
        f"SELECT {bucket} AS label, {measure_expr(measure)} AS value "
        f"FROM {ident(table.name)}{scoped_where} "
        f"GROUP BY {bucket} ORDER BY 1"
    )


def build_matrix_sql(
    *,
    table: BITableMeta,
    where: str,
    measure: Optional[str],
    date_column: str,
    group_column: str,
) -> str:
    group_sql = f"CAST({ident(group_column)} AS TEXT)"
    day_sql = date_part_expr(ident(date_column), "day")
    return (
        f"SELECT {group_sql} AS g, {day_sql} AS d, {measure_expr(measure)} AS value "
        f"FROM {ident(table.name)}{where} GROUP BY {group_sql}, {day_sql}"
    )


def build_cross_sql(
    *,
    table: BITableMeta,
    where: str,
    measure: Optional[str],
    group_column: str,
    category_column: str,
) -> str:
    group_sql = f"CAST({ident(group_column)} AS TEXT)"
    category_sql = f"CAST({ident(category_column)} AS TEXT)"
    return (
        f"SELECT {group_sql} AS g, {category_sql} AS c, "
        f"{measure_expr(measure)} AS value "
        f"FROM {ident(table.name)}{where} "
        f"GROUP BY {group_sql}, {category_sql}"
    )


def build_distinct_values_sql(
    table: BITableMeta,
    column_name: str,
    limit: int = 100,
) -> str:
    column_sql = f"CAST({ident(column_name)} AS TEXT)"
    return (
        f"SELECT DISTINCT {column_sql} AS v FROM {ident(table.name)} "
        f"WHERE {ident(column_name)} IS NOT NULL ORDER BY 1 LIMIT {int(limit)}"
    )


def build_detail_sql(
    *,
    table: BITableMeta,
    where: str,
    columns: Sequence[BIColumnMeta],
    date_column: Optional[str],
    limit: int,
) -> str:
    selected = ", ".join(ident(column.name) for column in columns)
    order = f" ORDER BY {ident(date_column)} DESC" if date_column else ""
    return (
        f"SELECT {selected} FROM {ident(table.name)}{where}{order} "
        f"LIMIT {int(limit)}"
    )


def build_visual_sql(
    *,
    table: BITableMeta,
    spec: BIVisualSpec,
    blocked_columns: set,
    is_admin: bool,
    panel_filters: Optional[Sequence[BIFilter]] = None,
) -> Tuple[str, bool]:
    """Arma el SELECT agregado de un visual. Devuelve (sql, enmascarar_etiqueta).

    `panel_filters` son los filtros compartidos de la pestaña que ya
    matchean la tabla/base de este visual: pasan por el MISMO `resolve` que
    los filtros propios, así que una columna enmascarada de un no-admin se
    deniega igual (fail-closed).
    """

    def resolve(column_name: Optional[str], role: str, allow_masked: bool) -> Optional[BIColumnMeta]:
        if not column_name:
            return None
        column = table.column(column_name)
        if not column:
            if column_name.lower() in blocked_columns:
                raise BIAccessDenied(
                    f"No tienes permiso para usar la columna '{column_name}' ({role})."
                )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"La columna '{column_name}' no existe en la tabla '{table.name}'.",
            )
        if column.masked and not is_admin and not allow_masked:
            raise BIAccessDenied(
                f"No puedes filtrar por la columna confidencial '{column.name}'."
            )
        return column

    dimension = resolve(spec.dimension, "eje", allow_masked=True)
    measure_name = None if spec.measure in (None, "", "count") else spec.measure
    measure = resolve(measure_name, "valor", allow_masked=True)

    if spec.date_grain and dimension is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Para agrupar por fecha primero elige una columna de eje.",
        )
    if spec.date_grain and dimension is not None and dimension.kind != "date":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{dimension.name}' no es una columna de fecha.",
        )
    series = None
    if spec.series:
        if dimension is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Para apilar por serie primero elige una columna de eje.",
            )
        # La serie se muestra en la leyenda: una columna confidencial se
        # deniega para no-admin en vez de enmascararse etiqueta por etiqueta
        # (mismo criterio que los filtros, más simple y más seguro).
        series = resolve(spec.series, "serie", allow_masked=True)
        if series is not None and series.masked and not is_admin:
            raise BIAccessDenied(
                f"La columna de serie '{series.name}' es confidencial: "
                "elige otra columna para la serie."
            )
    if measure and measure.masked and not is_admin and spec.aggregation not in (
        "count",
        "count_distinct",
    ):
        raise BIAccessDenied(
            f"La columna '{measure.name}' es confidencial: solo admite Contar o "
            "Contar distintos."
        )
    if (
        measure
        and dimension is not None
        and spec.aggregation in ("sum", "avg")
        and measure.kind == "text"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"'{measure.name}' no es numérica: usa Contar, Contar distintos, "
                "Mínimo o Máximo."
            ),
        )

    aggregation = spec.aggregation
    if measure is None:
        aggregation = "count"
    value_sql = measure_expr(measure.name if measure else None, aggregation)

    def filter_conditions(items: Sequence[BIFilter]) -> List[str]:
        """Condiciones WHERE de una lista de filtros, validadas contra el esquema."""
        conditions: List[str] = []
        for item in items:
            filter_column = resolve(item.column, "filtro", allow_masked=False)
            if filter_column is None:
                continue
            if item.value in (None, ""):
                # Un filtro recién agregado no debe romper la vista previa.
                continue
            if filter_column.kind == "number" and item.op != "contains":
                try:
                    float(str(item.value))
                except (TypeError, ValueError):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            f"El filtro de '{filter_column.name}' necesita un valor "
                            "numérico."
                        ),
                    )
            column_sql = ident(filter_column.name)
            if item.op == "contains":
                pattern = sql_literal(f"%{item.value}%", "text")
                conditions.append(f"CAST({column_sql} AS TEXT) ILIKE {pattern}")
            else:
                value = sql_literal(item.value, filter_column.kind)
                conditions.append(f"{column_sql} {item.op} {value}")
        return conditions

    all_conditions = filter_conditions(spec.filters) + filter_conditions(
        panel_filters or []
    )
    where_sql = f" WHERE {' AND '.join(all_conditions)}" if all_conditions else ""

    from_sql = ident(table.name)
    if dimension is None:
        return (
            f"SELECT {value_sql} AS value FROM {from_sql}{where_sql} LIMIT 1",
            False,
        )

    dimension_sql = ident(dimension.name)
    if spec.date_grain:
        dimension_sql = date_bucket_expr(dimension_sql, spec.date_grain)
    mask_label = bool(
        dimension.masked and not is_admin and not spec.date_grain
    )
    if series is not None:
        # Apiladas: agrupan por eje Y serie. El orden es fijo (eje, serie) para
        # que el pivot del frontend sea determinista; `spec.sort` no aplica.
        series_sql = f"CAST({ident(series.name)} AS TEXT)"
        sql = (
            f"SELECT {dimension_sql} AS label, {series_sql} AS series, "
            f"{value_sql} AS value "
            f"FROM {from_sql}{where_sql} "
            f"GROUP BY {dimension_sql}, {series_sql} "
            f"ORDER BY label ASC, series ASC LIMIT {int(spec.limit)}"
        )
        return sql, mask_label
    order_sql = {
        "value_desc": "value DESC",
        "value_asc": "value ASC",
        "label_asc": "label ASC",
        "label_desc": "label DESC",
    }[spec.sort]
    sql = (
        f"SELECT {dimension_sql} AS label, {value_sql} AS value "
        f"FROM {from_sql}{where_sql} "
        f"GROUP BY {dimension_sql} ORDER BY {order_sql} LIMIT {int(spec.limit)}"
    )
    return sql, mask_label

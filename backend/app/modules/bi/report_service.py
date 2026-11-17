"""Servicio del informe BI automático."""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fastapi import HTTPException, status

from app.api.deps import AuditWriteError, _persist_audit_log
from app.core.config import settings
from app.core.security import mask_query_rows
from app.modules.bi import sql_builder
from app.modules.bi.governance import (
    BIAccessDenied,
    BIContext,
    BITableMeta,
    is_period_like,
    load_context,
    report_columns,
    validate_column,
    validate_table,
)
from app.modules.bi.schemas import (
    BIColumn,
    BIConnectionRef,
    BICross,
    BICrossRow,
    BIDetail,
    BIKpis,
    BIMatrix,
    BIMatrixRow,
    BIOptions,
    BIPeriod,
    BIPoint,
    BIReportResponse,
    BIRoles,
    BITable,
)
from app.modules.chat_engine.ast_validator import ASTValidationError, ASTValidator
from app.modules.chat_engine.sql_executor import SQLExecutor

MAX_DETAIL_ROWS = 200
MAX_AUTO_CANDIDATES = 12
MAX_GROUP_ROWS = 25
MAX_CATEGORY_ROWS = 12
MAX_OPTIONS = 100


@dataclass
class _QueryRunner:
    """Ejecuta SQL del informe pasando por el mismo guardarraíl que el chat."""

    context: BIContext
    table: BITableMeta
    executed_sql: List[str] = field(default_factory=list)

    def run(self, sql: str) -> List[Dict[str, Any]]:
        _valid, secured_sql, _meta = ASTValidator.validate_and_secure_sql(
            sql,
            dialect="postgres",
            allowed_tables=self.context.allowed_tables,
            blocked_columns=self.context.blocked_columns,
            table_columns=self.context.table_columns,
            max_limit=settings.DEFAULT_ROW_LIMIT,
            is_admin=self.context.is_admin,
        )
        rows = SQLExecutor.execute_raw_sql(
            self.context.connection, secured_sql, dialect="postgres"
        )
        self.executed_sql.append(secured_sql)
        return rows


def _label(value: Any) -> str:
    return "(vacío)" if value is None else str(value)


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> Optional[int]:
    number = _number(value)
    return int(number) if number is not None else None


def _audit(
    db,
    user,
    context: BIContext,
    table_name: str,
    executed_sql: Sequence[str],
    validation_status: str,
    elapsed_ms: int,
    rows_returned: int = 0,
    error_message: Optional[str] = None,
    *,
    required: bool,
) -> Optional[int]:
    try:
        return _persist_audit_log(
            db=db,
            user_id=user.id,
            username=user.username,
            user_role=context.role_name,
            question_prompt=f"Informe BI: {context.connection.name} / {table_name}",
            sql_generated="\n\n".join(executed_sql) if executed_sql else None,
            validation_status=validation_status,
            target_database=context.connection.name or "",
            execution_time_ms=elapsed_ms,
            rows_returned=rows_returned,
            error_message=error_message,
            required=required,
        )
    except AuditWriteError:
        # Si la auditoría de un camino que devuelve datos falla, la respuesta
        # no sale. Es el mismo contrato que el chat.
        raise
    except Exception:
        if required:
            raise
        return None


def _best_cardinality(
    values: Dict[str, int],
    minimum: int,
    maximum: int,
    exclude: Sequence[str],
) -> Optional[str]:
    excluded = {name.lower() for name in exclude if name}
    candidates = [
        (name, count)
        for name, count in values.items()
        if minimum <= count <= maximum and name.lower() not in excluded
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[1], item[0].lower()))
    return candidates[0][0]


def _table_model(table: BITableMeta) -> BITable:
    return BITable(
        name=table.name,
        columns=[
            BIColumn(
                name=column.name,
                type=column.type,
                kind=column.kind,
                masked=column.masked,
            )
            for column in table.columns
        ],
    )


def _point_rows(rows: Sequence[Dict[str, Any]]) -> List[BIPoint]:
    return [
        BIPoint(label=_label(row.get("label")), value=_number(row.get("value")))
        for row in rows
    ]


def _pivot_matrix(rows: Sequence[Dict[str, Any]]) -> BIMatrix:
    days = set()
    data: Dict[str, Dict[str, float]] = {}
    for row in rows:
        group = _label(row.get("g"))
        day = row.get("d")
        if day is None:
            continue
        day_key = str(day)
        value = _number(row.get("value")) or 0.0
        days.add(day_key)
        data.setdefault(group, {})[day_key] = value
    matrix_rows = [
        BIMatrixRow(
            label=group,
            cells=cells,
            total=round(sum(cells.values()), 2),
        )
        for group, cells in sorted(data.items(), key=lambda item: item[0].casefold())
    ]
    return BIMatrix(days=sorted(days), rows=matrix_rows)


def _pivot_cross(
    rows: Sequence[Dict[str, Any]],
    top_categories: Sequence[str],
) -> BICross:
    top = list(top_categories)
    top_set = set(top)
    grouped: Dict[str, Dict[str, float]] = {}
    has_other = False
    for row in rows:
        group = _label(row.get("g"))
        category = _label(row.get("c"))
        if category not in top_set:
            category = "Otros"
            has_other = True
        value = _number(row.get("value")) or 0.0
        cells = grouped.setdefault(group, {})
        cells[category] = cells.get(category, 0.0) + value

    categories = top + (["Otros"] if has_other else [])
    cross_rows = [
        BICrossRow(
            label=group,
            cells={key: round(value, 2) for key, value in cells.items()},
            total=round(sum(cells.values()), 2),
        )
        for group, cells in grouped.items()
    ]
    cross_rows.sort(key=lambda row: (-row.total, row.label.casefold()))
    return BICross(categories=categories, rows=cross_rows)


def _resolve_columns(
    context: BIContext,
    table: BITableMeta,
    date_column: Optional[str],
    group_column: Optional[str],
    category_column: Optional[str],
    measure: Optional[str],
    runner: _QueryRunner,
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Resuelve fecha, grupo, categoría y medida, con autoselección si faltan."""
    columns = report_columns(table, context.is_admin)
    by_name = {column.name.lower(): column for column in columns}

    def deny_blocked(value: Optional[str], role: str) -> None:
        if value and value.lower() in context.blocked_columns:
            raise BIAccessDenied(
                f"No tienes permiso para usar la columna '{value}' como {role}."
            )

    def explicit(value: Optional[str], role: str) -> Optional[str]:
        if value in (None, ""):
            return None
        deny_blocked(value, role)
        column = validate_column(
            table,
            value,
            role=role,
            allow_masked=context.is_admin,
        )
        if column.name.lower() not in by_name:
            # Una columna MASKED de un no-admin no está en report_columns.
            raise BIAccessDenied(
                f"No tienes permiso para usar la columna confidencial "
                f"'{column.name}' como {role}."
            )
        return column.name

    resolved_date: Optional[str]
    if date_column == "-":
        resolved_date = None
    elif date_column:
        deny_blocked(date_column, "fecha")
        column = validate_column(
            table, date_column, role="fecha", allow_masked=context.is_admin
        )
        if column.kind != "date":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"'{column.name}' no es una columna de fecha.",
            )
        resolved_date = column.name
    else:
        resolved_date = next(
            (column.name for column in columns if column.kind == "date"), None
        )

    resolved_group = explicit(group_column, "grupo")
    resolved_category = explicit(category_column, "categoría")

    resolved_measure: Optional[str] = None
    if measure not in (None, "", "count"):
        deny_blocked(measure, "medida")
        column = validate_column(
            table, measure, role="medida", allow_masked=context.is_admin
        )
        if column.kind != "number":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"La medida '{column.name}' debe ser una columna numérica.",
            )
        if not context.is_admin and column.name.lower() not in by_name:
            raise BIAccessDenied(
                f"No tienes permiso para usar la columna confidencial "
                f"'{column.name}' como medida."
            )
        resolved_measure = column.name

    text_columns = [
        column
        for column in columns
        if column.kind == "text" and not is_period_like(column.name)
    ][:MAX_AUTO_CANDIDATES]

    cardinalities: Dict[str, int] = {}
    if (resolved_group is None or resolved_category is None) and text_columns:
        cardinality_sql = sql_builder.build_cardinality_sql(table, text_columns)
        if cardinality_sql:
            rows = runner.run(cardinality_sql)
            if rows:
                raw = rows[0]
                for index, column in enumerate(text_columns):
                    count = _int(raw.get(f"c{index}"))
                    if count is not None:
                        cardinalities[column.name] = count

    if resolved_group is None:
        resolved_group = _best_cardinality(
            cardinalities, minimum=3, maximum=30, exclude=[]
        )
    if resolved_category is None:
        resolved_category = _best_cardinality(
            cardinalities,
            minimum=2,
            maximum=10,
            exclude=[resolved_group] if resolved_group else [],
        ) or _best_cardinality(
            cardinalities,
            minimum=2,
            maximum=15,
            exclude=[resolved_group] if resolved_group else [],
        )
    return resolved_date, resolved_group, resolved_category, resolved_measure


def build_report(
    *,
    db,
    user,
    connection_id: int,
    table_name: str,
    date_column: Optional[str] = None,
    group_column: Optional[str] = None,
    category_column: Optional[str] = None,
    measure: Optional[str] = None,
    year: Optional[int] = None,
    month: Optional[str] = None,
    group_value: Optional[str] = None,
    category_value: Optional[str] = None,
) -> BIReportResponse:
    """Construye el informe completo de una tabla.

    El orden importa: primero se resuelve la gobernanza, después se construye y
    ejecuta cada consulta con el mismo guardarraíl del chat, y al final se
    escribe la auditoría. Un error de validación o ejecución también se audita,
    pero nunca devuelve datos.
    """
    context = load_context(db, user, connection_id, "Informe BI")
    started = time.perf_counter()
    runner: Optional[_QueryRunner] = None
    table: Optional[BITableMeta] = None
    detail_rows = 0

    try:
        table = validate_table(context, table_name)
        runner = _QueryRunner(context=context, table=table)

        date_col, group_col, category_col, measure_col = _resolve_columns(
            context,
            table,
            date_column,
            group_column,
            category_column,
            measure,
            runner,
        )

        years: List[int] = []
        months: List[int] = []
        resolved_year: Optional[int] = None
        resolved_month: Optional[int] = None

        if date_col:
            year_rows = runner.run(
                sql_builder.build_years_sql(table, date_col)
            )
            years = sorted(
                {
                    int(value)
                    for value in (_int(row.get("y")) for row in year_rows)
                    if value is not None
                },
                reverse=True,
            )
            if year is not None:
                if year not in years:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"El año {year} no tiene datos en esta tabla.",
                    )
                resolved_year = year
            else:
                resolved_year = years[0] if years else None

            if resolved_year is not None:
                month_rows = runner.run(
                    sql_builder.build_months_sql(table, date_col, resolved_year)
                )
                months = sorted(
                    {
                        int(value)
                        for value in (_int(row.get("m")) for row in month_rows)
                        if value is not None
                    }
                )
                if month == "all":
                    resolved_month = None
                elif month not in (None, ""):
                    try:
                        parsed_month = int(month)
                    except (TypeError, ValueError):
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="El mes debe ser un número entre 1 y 12, o 'all'.",
                        )
                    if parsed_month < 1 or parsed_month > 12:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="El mes debe estar entre 1 y 12.",
                        )
                    if parsed_month not in months:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=(
                                f"El mes {parsed_month} no tiene datos en "
                                f"{resolved_year}."
                            ),
                        )
                    resolved_month = parsed_month
                else:
                    resolved_month = months[-1] if months else None

        where = sql_builder.build_where(
            date_column=date_col,
            group_column=group_col,
            category_column=category_col,
            year=resolved_year,
            month=resolved_month,
            group_value=group_value,
            category_value=category_value,
        )

        options_group: List[str] = []
        options_category: List[str] = []
        if group_col:
            options_group = [
                _label(row.get("v"))
                for row in runner.run(
                    sql_builder.build_distinct_values_sql(
                        table, group_col, MAX_OPTIONS
                    )
                )
            ]
        if category_col:
            options_category = [
                _label(row.get("v"))
                for row in runner.run(
                    sql_builder.build_distinct_values_sql(
                        table, category_col, MAX_OPTIONS
                    )
                )
            ]

        kpi_row = runner.run(
            sql_builder.build_kpis_sql(
                table=table,
                where=where,
                measure=measure_col,
                group_column=group_col,
                date_column=date_col,
            )
        )[0]
        registros = _int(kpi_row.get("n")) or 0
        total = _number(kpi_row.get("total"))
        grupos = _int(kpi_row.get("grupos"))
        dias = _int(kpi_row.get("dias"))
        promedio_diario = (
            round(total / dias, 2)
            if total is not None and dias and dias > 0
            else None
        )
        promedio_por_grupo = (
            round(total / grupos, 2)
            if total is not None and grupos and grupos > 0
            else None
        )

        # Tendencia contra el período anterior: mes previo si hay mes elegido,
        # año previo si se mira todo el año. Los filtros de grupo y categoría
        # se respetan (son de página, como los slicers de Power BI). Sin
        # historial comparable no se inventa: `None` y el KPI no muestra delta.
        total_anterior: Optional[float] = None
        delta_pct: Optional[float] = None
        if date_col and resolved_year is not None:
            if resolved_month is not None and resolved_month > 1:
                prev_year, prev_month = resolved_year, resolved_month - 1
            elif resolved_month is not None:
                prev_year, prev_month = resolved_year - 1, 12
            else:
                prev_year, prev_month = resolved_year - 1, None
            previous_where = sql_builder.build_where(
                date_column=date_col,
                group_column=group_col,
                category_column=category_col,
                year=prev_year,
                month=prev_month,
                group_value=group_value,
                category_value=category_value,
            )
            previous_row = runner.run(
                sql_builder.build_kpis_sql(
                    table=table,
                    where=previous_where,
                    measure=measure_col,
                    group_column=None,
                    date_column=None,
                )
            )[0]
            total_anterior = _number(previous_row.get("total"))
            if total is not None and total_anterior not in (None, 0):
                delta_pct = round((total - total_anterior) / total_anterior * 100, 2)

        by_group: List[BIPoint] = []
        if group_col:
            by_group = _point_rows(
                runner.run(
                    sql_builder.build_group_sql(
                        table=table,
                        where=where,
                        measure=measure_col,
                        group_column=group_col,
                        limit=MAX_GROUP_ROWS,
                    )
                )
            )

        by_category: List[BIPoint] = []
        if category_col:
            by_category = _point_rows(
                runner.run(
                    sql_builder.build_category_sql(
                        table=table,
                        where=where,
                        measure=measure_col,
                        category_column=category_col,
                        limit=MAX_CATEGORY_ROWS,
                    )
                )
            )

        daily: List[BIPoint] = []
        if date_col:
            daily = _point_rows(
                runner.run(
                    sql_builder.build_daily_sql(
                        table=table,
                        where=where,
                        measure=measure_col,
                        date_column=date_col,
                        month=resolved_month,
                    )
                )
            )

        matrix: Optional[BIMatrix] = None
        if date_col and group_col and resolved_month:
            matrix = _pivot_matrix(
                runner.run(
                    sql_builder.build_matrix_sql(
                        table=table,
                        where=where,
                        measure=measure_col,
                        date_column=date_col,
                        group_column=group_col,
                    )
                )
            )

        cross: Optional[BICross] = None
        if group_col and category_col:
            cross = _pivot_cross(
                runner.run(
                    sql_builder.build_cross_sql(
                        table=table,
                        where=where,
                        measure=measure_col,
                        group_column=group_col,
                        category_column=category_col,
                    )
                ),
                [point.label for point in by_category],
            )

        detail_columns = report_columns(table, context.is_admin)
        detail = BIDetail(columns=[column.name for column in detail_columns], rows=[])
        if detail_columns:
            detail_sql = sql_builder.build_detail_sql(
                table=table,
                where=where,
                columns=detail_columns,
                date_column=date_col,
                limit=MAX_DETAIL_ROWS,
            )
            detail_rows_list = runner.run(detail_sql)
            # Defensa en profundidad: el SELECT ya no proyecta columnas MASKED,
            # pero todo camino que devuelve filas pasa por el sink de masking.
            mask_query_rows(
                detail_rows_list,
                context.masked_columns,
                runner.executed_sql[-1],
                dialect="postgres",
            )
            detail_rows = len(detail_rows_list)
            detail = BIDetail(
                columns=[column.name for column in detail_columns],
                rows=detail_rows_list,
            )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        audit_id = _audit(
            db,
            user,
            context,
            table.name,
            runner.executed_sql,
            "APROBADO",
            elapsed_ms,
            rows_returned=detail_rows,
            required=True,
        )

        return BIReportResponse(
            connection=BIConnectionRef(
                id=context.connection.id, name=context.connection.name
            ),
            table=_table_model(table),
            roles=BIRoles(
                date=date_col,
                group=group_col,
                category=category_col,
                measure=measure_col or "count",
            ),
            period=BIPeriod(
                years=years,
                months=months,
                year=resolved_year,
                month=resolved_month,
            ),
            options=BIOptions(group=options_group, category=options_category),
            kpis=BIKpis(
                registros=registros,
                total=total,
                total_anterior=total_anterior,
                delta_pct=delta_pct,
                grupos=grupos,
                dias=dias,
                promedio_diario=promedio_diario,
                promedio_por_grupo=promedio_por_grupo,
            ),
            by_group=by_group,
            by_category=by_category,
            daily=daily,
            matrix=matrix,
            cross=cross,
            detail=detail,
            audit_log_id=audit_id,
        )
    except AuditWriteError:
        # La auditoría de un camino que devuelve datos falló: la respuesta no
        # sale. No se reconvierte en un 400 ni se reintenta sin trazabilidad.
        raise
    except BIAccessDenied as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        _audit(
            db,
            user,
            context,
            table.name if table else table_name,
            runner.executed_sql if runner else [],
            "RECHAZADO_RBAC",
            elapsed_ms,
            error_message=str(exc.detail),
            required=False,
        )
        raise
    except ASTValidationError as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        _audit(
            db,
            user,
            context,
            table.name if table else table_name,
            runner.executed_sql if runner else [],
            "RECHAZADO_RBAC",
            elapsed_ms,
            error_message=str(exc),
            required=False,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"La consulta del informe fue rechazada: {exc}",
        )
    except HTTPException:
        raise
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        _audit(
            db,
            user,
            context,
            table.name if table else table_name,
            runner.executed_sql if runner else [],
            "ERROR_EJECUCION",
            elapsed_ms,
            error_message=str(exc),
            required=False,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No se pudo ejecutar el informe: {exc}",
        )

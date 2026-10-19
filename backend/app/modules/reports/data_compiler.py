from typing import Union, List, Tuple
from app.modules.admin_catalog.schemas import ReportExportData, ReportExportRequest

class ReportDataCompiler:
    """
    Compiles and sanitizes dataset metrics, executive reports, and traceability details for export.
    """

    # ponytail: un unico lugar para "no lo se". Ningun export de este modulo puede
    # afirmar un resultado que no tiene dato de respaldo.
    SIN_DATO = "No disponible"
    SIN_VALIDACION = "Sin registro de validación"

    @classmethod
    def compile_summary_text(cls, data: Union[ReportExportData, ReportExportRequest]) -> str:
        if data.executive_report and data.executive_report.overview:
            return data.executive_report.overview
        if data.summary_text:
            return data.summary_text
        # ponytail: sin resumen no se inventa exito. Antes decia "se procesó
        # satisfactoriamente sobre el esquema corporativo autorizado".
        return "No hay resumen disponible para esta consulta."

    @classmethod
    def compile_db_name(cls, data: Union[ReportExportData, ReportExportRequest]) -> str:
        """Nombre real de la base. Nunca se inventa: si no viene, 'No disponible'."""
        return getattr(data, "target_database", None) or cls.SIN_DATO

    @classmethod
    def compile_trace_line(cls, data: Union[ReportExportData, ReportExportRequest]) -> str:
        """Linea de trazabilidad sin defaults que affirmen un resultado."""
        trace = data.traceability
        status = (trace.validation_status if trace else None) or cls.SIN_VALIDACION
        rows = trace.rows_returned if trace else None
        ms = trace.execution_time_ms if trace else None
        return (
            f"Estado AST: {status} | "
            f"Filas: {cls.SIN_DATO if rows is None else rows} | "
            f"Latencia: {cls.SIN_DATO if ms is None else f'{ms} ms'}"
        )

    @classmethod
    def compile_findings(cls, data: Union[ReportExportData, ReportExportRequest]) -> List[str]:
        findings = data.executive_report.key_findings if (data.executive_report and data.executive_report.key_findings) else []
        if not findings and data.data_rows:
            findings = [
                f"Se procesaron {len(data.data_rows)} registros de la base de datos activa.",
                f"Columnas analizadas: {', '.join(data.data_columns[:5])}."
            ]
        if not findings and getattr(data, "include_raw_data", True) is False:
            # ponytail: con include_raw_data=False `generator.py` vacia data_rows
            # ANTES de compilar. Devolver [] hacia que el informe presentara "sin
            # hallazgos" cuando los hallazgos nunca se calcularon: dos entregables
            # del mismo dato con contenido distinto y sin declaracion. Se declara
            # la ausencia en vez de fingir un analisis sin filas.
            return [
                "No se incluyen hallazgos: este informe se generó con "
                "include_raw_data=False, por lo que los datos crudos de la consulta "
                "no se exportaron y no se corrió ningún análisis sobre ellos. "
                "El informe ejecutivo con hallazgos requiere include_raw_data=True."
            ]
        return findings

    @classmethod
    def compile_recommendations(cls, data: Union[ReportExportData, ReportExportRequest]) -> List[str]:
        return data.executive_report.recommendations if (data.executive_report and data.executive_report.recommendations) else []

    @classmethod
    def compile_columns(cls, data: Union[ReportExportData, ReportExportRequest]) -> List[str]:
        columns = data.data_columns
        if not columns and data.data_rows:
            columns = list(data.data_rows[0].keys())
        return columns or []

    # --- Proyeccion ML (snapshot de /ml/projections/run) -------------------
    #
    # El snapshot de una proyeccion no tiene filas: tiene serie, metodo y pasos
    # con distinta cantidad de evidencia. Estas helpers son el unico lugar que
    # decide como se escribe cada caso y las comparten PDF y Excel, para que los
    # dos documentos digan exactamente lo mismo. Un paso sin backtest propio se
    # declara "No disponible" en sus celdas: un 0 seria una afirmacion sobre el
    # futuro que nadie midio.

    @classmethod
    def fmt_number(cls, value) -> str:
        """Numero legible es-CL sin inventar moneda (la metrica puede ser montos
        o unidades): 1234.5 -> "1.235". None -> "No disponible"."""
        if value is None:
            return cls.SIN_DATO
        try:
            return f"{float(value):,.0f}".replace(",", ".")
        except (TypeError, ValueError):
            return str(value)

    @classmethod
    def fmt_pct(cls, value) -> str:
        """Porcentaje es-CL con un decimal: 12.55 -> "12,6%". None -> "No disponible"."""
        if value is None:
            return cls.SIN_DATO
        try:
            texto = f"{float(value):,.1f}"
        except (TypeError, ValueError):
            return str(value)
        return texto.replace(",", "\u00a0").replace(".", ",").replace("\u00a0", ".") + "%"

    @classmethod
    def compile_projection_facts(cls, data: Union[ReportExportData, ReportExportRequest]) -> List[Tuple[str, str]]:
        """Pares (etiqueta, valor) de la corrida. Sin dato = SIN_DATO, nunca 0.

        Son las mismas etiquetas que la pantalla de Proyecciones publica: metodo,
        si le gano al naive, MAPE, periodos, horizonte, huecos y origen del
        analisis. El lector del informe tiene que poder auditar la evidencia sin
        volver a la pantalla.
        """
        p = getattr(data, "projection", None)
        if p is None:
            return []

        verdict = {True: "Sí, el método publicado superó al naive en el backtest.",
                   False: "No, ningún método superó al naive en el backtest."}.get(
            p.beats_naive, "No medido: no hubo backtest comparable contra el naive.")

        eje = cls.SIN_DATO
        if p.table:
            eje = p.table
            if p.metric_column:
                eje += f".{p.metric_column}"
            if p.date_column:
                eje += f" por {p.date_column}"

        origen = {"llm": "modelo local (LLM)", "heuristica": "heurística del servidor"}.get(
            p.analysis_source or "", p.analysis_source or cls.SIN_DATO)

        facts = [
            ("Método publicado", p.method or cls.SIN_DATO),
            ("¿Superó al método naive?", verdict),
            ("MAPE del método publicado", cls.fmt_pct(p.mape)),
            ("Periodos históricos analizados", str(p.n_periods)),
            ("Horizonte publicado", f"{p.horizon} pasos"),
            ("Serie económica", "solo ingresos (montos positivos)" if p.income_only
             else "montos netos (incluye valores negativos)"),
            ("Continuidad de la serie", "Sin huecos en el eje temporal." if not p.has_gaps
             else "Con huecos: el error medido puede no cubrir la serie completa."),
            ("Serie analizada", eje),
            ("Elección del análisis", origen),
        ]
        if p.mape_by_method:
            candidatos = ", ".join(
                f"{metodo}: {cls.fmt_pct(valor)}" for metodo, valor in p.mape_by_method.items()
            )
            facts.append(("Candidatos evaluados (MAPE)", candidatos))
        return facts

    @classmethod
    def compile_projection_step_rows(cls, data: Union[ReportExportData, ReportExportRequest]) -> List[List[str]]:
        """Filas [Paso, Periodo, Punto, Banda, ±%, MAPE, Backtests, Evidencia].

        Mismos textos que la tabla de la pantalla: "sin backtest propio", "poca
        evidencia", "medida" — para que el informe no afirme mas que la UI.
        """
        rows = []
        proyeccion = getattr(data, "projection", None)
        if proyeccion is None:
            return rows
        for s in proyeccion.steps:
            if s.extrapolated:
                evidencia = "Sin backtest propio (banda heredada del último medido)"
            elif not s.reliable:
                evidencia = "Poca evidencia (menos de 4 backtests)"
            else:
                evidencia = "Medida (backtest propio)"
            banda = (
                cls.SIN_DATO if s.lower is None or s.upper is None
                # Entre parentesis por un motivo concreto: una banda que cruza
                # el cero empieza por "-" y el escape anti-formulas del export
                # le antepone un apostrofe que queda A LA VISTA en Excel,
                # mutilando un dato legitimo. Con parentesis el texto nunca
                # arranca con un prefijo de formula y el sanitizador sigue
                # neutralizando lo que venga de la base del cliente.
                else f"({cls.fmt_number(s.lower)} – {cls.fmt_number(s.upper)})"
            )
            rows.append([
                f"Paso {s.step}",
                s.period or "Sin periodo",
                cls.fmt_number(s.point),
                banda,
                cls.SIN_DATO if s.band_pct is None else f"±{cls.fmt_pct(s.band_pct)}",
                cls.fmt_pct(s.mape),
                str(s.n_backtests),
                evidencia,
            ])
        return rows

    @classmethod
    def compile_projection_series_rows(cls, data: Union[ReportExportData, ReportExportRequest]) -> List[List[str]]:
        """Filas [Periodo, Valor] de la serie historica publicada, completa.

        Sin truncar: si una serie larga no cabe en una pagina, el PDF pagina; lo
        que no puede pasar es que el documento muestre un subconjunto distinto
        del que muestra la pantalla sin decirlo.
        """
        rows = []
        proyeccion = getattr(data, "projection", None)
        if proyeccion is None:
            return rows
        for punto in proyeccion.series:
            periodo, valor = None, None
            if isinstance(punto, dict):
                periodo, valor = punto.get("periodo"), punto.get("valor")
            rows.append([periodo if periodo else "Sin periodo", cls.fmt_number(valor)])
        return rows

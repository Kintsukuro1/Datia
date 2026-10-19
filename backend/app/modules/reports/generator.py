import json
import datetime
from typing import Tuple
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.modules.auth.models import User
from app.modules.telemetry_audit.models import AuditLog
from app.api.deps import display_role_name
from app.modules.admin_catalog.schemas import (
    ReportExportData, ReportExportRequest, ProjectionExportData, TraceabilityAuditData,
)
from app.modules.reports.pdf_exporter import PDFExporter
from app.modules.reports.excel_exporter import ExcelExporter

class ReportGeneratorService:
    """
    Generates professional, presentation-ready PDF reports and structured Excel (.xlsx) workbooks.
    Handles audit_log_id lookup, permissions checking, and export audit logging.
    """

    @classmethod
    def _audit_rows_returned(cls, data_obj: ReportExportData, original_rows: int) -> int:
        """Filas que devolvio la consulta, no las que se exportaron.

        `traceability.rows_returned` es la fuente (es lo que arma
        chat_engine/router.py:110). Si el snapshot no lo trae, o trae 0 pero el
        snapshot si tiene filas, gana el conteo real: auditar 0 sobre una consulta
        que devolvio 5000 filas es justamente el dato falsificado que se
        corrige aca.
        """
        if data_obj.traceability and data_obj.traceability.rows_returned:
            return data_obj.traceability.rows_returned
        return original_rows

    # `ml_forecast/router.py` marca con este estado los DOS caminos de una
    # proyeccion (publicada y no disponible). Es la unica senal de que el
    # snapshot tiene forma de proyeccion y no de consulta.
    PROJECTION_STATUS = "PROYECCION_ML"

    @classmethod
    def _build_export_data(cls, data_dict: dict, log_entry: AuditLog) -> ReportExportData:
        """Arma el ReportExportData del snapshot auditado, sea consulta o proyeccion.

        Una proyeccion ML no tiene filas ni KPIs: con la construccion generica
        (`ReportExportData(**snapshot)`) el documento salia hueco — pregunta y
        nada mas — mientras afirmaba ser un informe ejecutivo de una corrida que
        si tenia serie, banda y MAPE. Aca se compila su propia seccion desde el
        snapshot; de este punto en adelante los dos caminos son el mismo
        documento.
        """
        if log_entry.validation_status != cls.PROJECTION_STATUS:
            return ReportExportData(**data_dict)

        projection = ProjectionExportData(**{
            nombre: data_dict[nombre]
            for nombre in ProjectionExportData.model_fields
            if nombre in data_dict
        })

        # El Diagnostico del informe muestra la narrativa; su origen se declara
        # al lado para que el lector sepa si la redacto el modelo local o una
        # plantilla del servidor (misma regla que la pantalla).
        narrativa = projection.narration or "No hay resumen disponible para esta consulta."
        if projection.narration and projection.narration_source:
            fuente = {"llm": "modelo local",
                      "plantilla": "plantilla del servidor"}.get(
                projection.narration_source, projection.narration_source)
            narrativa = f"{narrativa} (Narrativa: {fuente}.)"

        return ReportExportData(
            question=data_dict.get("question") or "Proyección ML",
            summary_text=narrativa,
            traceability=TraceabilityAuditData(
                sql_executed=data_dict.get("sql") or "",
                execution_time_ms=log_entry.execution_time_ms,
                # Una proyeccion no devuelve filas de consulta: None para que la
                # linea de trazabilidad diga "No disponible" y no un 0 no medido.
                rows_returned=None,
                validation_status=log_entry.validation_status,
            ),
            projection=projection,
            target_database=log_entry.target_database,
            chart_image_base64=data_dict.get("chart_image_base64"),
            custom_notes=data_dict.get("custom_notes"),
            include_raw_data=data_dict.get("include_raw_data", True),
        )

    @classmethod
    def _prepare_export(
        cls, db: Session, current_user: User, req: ReportExportRequest, *, include_chart_image: bool
    ) -> Tuple[ReportExportData, int, AuditLog]:
        """Valida el pedido, carga el snapshot auditado y arma el documento.

        Un solo procedimiento para PDF y Excel: lookup del log, permisos
        (404/403), snapshot ausente o corrupto (409) y compilacion del
        `ReportExportData`. Devuelve tambien `original_rows` (las filas que
        devolvio la consulta, no las exportadas) para la fila de auditoria.
        """
        if not req.audit_log_id:
            # fail-closed: sin audit_log_id no hay documento. Antes caia en
            # `generate_pdf(req)` con un ReportExportRequest -> AttributeError -> 500.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Se requiere audit_log_id para exportar un informe."
            )
        log_entry = db.query(AuditLog).filter(AuditLog.id == req.audit_log_id).first()
        if not log_entry:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Registro de auditoría no encontrado."
            )
        if not current_user.is_admin and log_entry.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permisos para exportar consultas de otro usuario."
            )
        if not log_entry.result_snapshot:
            # ponytail: antes caia al `generate_pdf(req)`, que reventaba con
            # AttributeError -> 500. El log existe, pero sin resultado: eso es
            # 409, no un documento a medias.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Esta consulta no tiene resultado exportable: el registro de auditoría no guardó snapshot de la respuesta."
            )
        try:
            data_dict = json.loads(log_entry.result_snapshot)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Esta consulta no tiene resultado exportable: el snapshot del registro de auditoría está corrupto."
            )

        # El grafico embebido es solo del PDF; el Excel no incrusta la imagen.
        if include_chart_image and req.chart_image_base64:
            data_dict["chart_image_base64"] = req.chart_image_base64
        if req.custom_title:
            data_dict["question"] = req.custom_title
        if req.custom_notes:
            data_dict["custom_notes"] = req.custom_notes
        data_dict["include_raw_data"] = req.include_raw_data
        # ponytail: el audit graba lo que devolvio la consulta, no lo que se
        # exporto. include_raw_data=False vacia data_rows para el documento,
        # no para el registro de auditoria.
        original_rows = len(data_dict.get("data_rows") or [])
        if not req.include_raw_data:
            data_dict["data_rows"] = []
            data_dict["data_columns"] = []

        return cls._build_export_data(data_dict, log_entry), original_rows, log_entry

    @classmethod
    def _log_export(
        cls, db: Session, current_user: User, data_obj: ReportExportData, original_rows: int,
        log_entry: AuditLog, status_label: str
    ) -> None:
        """Fila de compliance de una exportacion, sobre el snapshot del log origen."""
        export_audit = AuditLog(
            user_id=current_user.id,
            username=current_user.username,
            # `display_role_name` y no un nombre inventado: esta fila es
            # evidencia de compliance, no una etiqueta de UI. Una exportacion
            # registrada como "Usuario" de una cuenta sin rol affirmaria un
            # perfil que la cuenta no tiene. Para la cuenta sin rol el
            # registro queda con `user_role = NULL`, que es lo que la columna
            # nullable admite y lo que los consumidores ya esperan: el CSV
            # de `telemetry_audit/router.py` escribe `log.user_role or ""`
            # y `AuditLogOut.user_role` es `Optional[str]`.
            user_role=display_role_name(current_user),
            question_prompt=data_obj.question,
            sql_generated=data_obj.traceability.sql_executed if data_obj.traceability else None,
            validation_status=status_label,
            target_database=data_obj.target_database,
            execution_time_ms=data_obj.traceability.execution_time_ms if data_obj.traceability else 0,
            rows_returned=cls._audit_rows_returned(data_obj, original_rows),
            result_snapshot=log_entry.result_snapshot
        )
        db.add(export_audit)
        db.commit()

    @classmethod
    def export_pdf(cls, db: Session, current_user: User, req: ReportExportRequest) -> Tuple[bytes, str]:
        data_obj, original_rows, log_entry = cls._prepare_export(db, current_user, req, include_chart_image=True)
        pdf_bytes = PDFExporter.generate_pdf(data_obj)
        cls._log_export(db, current_user, data_obj, original_rows, log_entry, "EXPORTADO_PDF")
        filename = f"informe_ejecutivo_datia_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf"
        return pdf_bytes, filename

    @classmethod
    def export_excel(cls, db: Session, current_user: User, req: ReportExportRequest) -> Tuple[bytes, str]:
        data_obj, original_rows, log_entry = cls._prepare_export(db, current_user, req, include_chart_image=False)
        excel_bytes = ExcelExporter.generate_excel(data_obj)
        cls._log_export(db, current_user, data_obj, original_rows, log_entry, "EXPORTADO_EXCEL")
        filename = f"datos_datia_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return excel_bytes, filename

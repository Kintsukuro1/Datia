import csv
import io
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
from main import app
from sqlalchemy import event

from app.core.database import SessionLocal, Base, engine
from app.db.init_db import init_db
from app.modules.telemetry_audit.models import AuditLog

from conftest import make_auth_headers

class TestAuditLog(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        db = SessionLocal()
        init_db(db)
        db.close()

    def setUp(self):
        self.client = TestClient(app)
        self.db = SessionLocal()

        # Admin con sesion viva: el preambulo comun vive en conftest.
        self.headers = make_auth_headers(self.db, "admin")

    def tearDown(self):
        self.db.close()

    def test_approved_query_persists_audit_log(self):
        """Approved query against demo endpoint records an AuditLog with validation_status APROBADO."""
        initial_count = self.db.query(AuditLog).count()

        headers = self.headers
        resp = self.client.post(
            "/api/v1/chat/query",
            json={"question": "hola buenos dias", "connection_id": 1},
            headers=headers
        )
        self.assertEqual(resp.status_code, 200)

        new_count = self.db.query(AuditLog).count()
        self.assertGreater(new_count, initial_count)

        latest_log = self.db.query(AuditLog).order_by(AuditLog.id.desc()).first()
        self.assertEqual(latest_log.username, "admin")
        self.assertTrue("APROBADO" in latest_log.validation_status)
        self.assertEqual(latest_log.question_prompt, "hola buenos dias")

    def test_rejected_rbac_query_persists_audit_log(self):
        """Rejected query (e.g. forbidden SQL) records an AuditLog with RECHAZADO status via /chat/query."""
        headers = make_auth_headers(self.db, "felipe_economista")
        resp = self.client.post(
            "/api/v1/chat/query",
            json={"question": "DROP TABLE fact_ventas", "connection_id": 1},
            headers=headers
        )
        self.assertEqual(resp.status_code, 200)

        latest_log = self.db.query(AuditLog).order_by(AuditLog.id.desc()).first()
        self.assertEqual(latest_log.username, "felipe_economista")
        self.assertTrue("RECHAZADO" in latest_log.validation_status or "ERROR" in latest_log.validation_status)

    def test_audit_list_and_export_endpoints(self):
        """Admin can list and export audit logs via /api/v1/audit and /api/v1/audit/export."""
        headers = self.headers

        # 1. List audit logs
        list_resp = self.client.get("/api/v1/audit?page=1&page_size=10", headers=headers)
        self.assertEqual(list_resp.status_code, 200)
        data = list_resp.json()
        self.assertIn("items", data)
        self.assertIn("total", data)
        self.assertGreaterEqual(len(data["items"]), 1)

        # 2. Export CSV
        csv_resp = self.client.get("/api/v1/audit/export", headers=headers)
        self.assertEqual(csv_resp.status_code, 200)
        self.assertIn("text/csv", csv_resp.headers.get("content-type", ""))
        self.assertIn("Fecha", csv_resp.text)
        self.assertIn("Usuario", csv_resp.text)

    # --- export: detalle, truncamiento y memoria -------------------------

    def _export_rows(self, marker: str):
        """Exporta filtrado por un username unico para no depender del volumen
        de la base de desarrollo, y devuelve (filas del CSV, texto crudo)."""
        resp = self.client.get(
            "/api/v1/audit/export",
            params={"username": marker},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 200, resp.text[:300])
        return list(csv.reader(io.StringIO(resp.text))), resp.text

    def _cleanup_logs(self, marker: str):
        self.db.query(AuditLog).filter(AuditLog.username == marker).delete(
            synchronize_session=False
        )
        self.db.commit()

    def test_export_incluye_detalle_de_eventos_de_permisos(self):
        """El documento de compliance tiene que decir QUE columna se toco.

        El detalle vive en `result_snapshot` y el CSV no lo llevaba: el inspector
        lo mostraba, el archivo exportado no. Un evento de permisos sin su
        detalle no sirve como evidencia.
        """
        marker = f"export-detalle-{uuid.uuid4().hex[:8]}"
        try:
            self.db.add_all([
                AuditLog(
                    username=marker,
                    question_prompt="Política de columna eliminada: t.rut",
                    validation_status="PERMISOS_COLUMNA",
                    target_database="db_prueba",
                    result_snapshot=(
                        '{"accion": "eliminar_politica_columna", '
                        '"columnas": ["t.rut"]}'
                    ),
                ),
                AuditLog(
                    username=marker,
                    question_prompt="consulta normal",
                    validation_status="APROBADO",
                    target_database="db_prueba",
                    result_snapshot="SNAPSHOT_DE_CONSULTA",
                ),
            ])
            self.db.commit()

            rows, raw = self._export_rows(marker)
            header = rows[0]
            self.assertIn("Detalle del Evento", header)
            detail_idx = header.index("Detalle del Evento")
            status_idx = header.index("Estado Validación")

            por_estado = {r[status_idx]: r for r in rows[1:] if r[0] != "AVISO"}
            self.assertIn("eliminar_politica_columna", por_estado["PERMISOS_COLUMNA"][detail_idx])
            self.assertIn("t.rut", por_estado["PERMISOS_COLUMNA"][detail_idx])
            self.assertEqual(por_estado["APROBADO"][detail_idx], "")
            self.assertNotIn("SNAPSHOT_DE_CONSULTA", raw)
        finally:
            self._cleanup_logs(marker)

    def test_export_avisa_cuando_trunca(self):
        """Un export cortado no puede presentarse como completo."""
        marker = f"export-truncado-{uuid.uuid4().hex[:8]}"
        try:
            self.db.add_all([
                AuditLog(username=marker, question_prompt=f"consulta {i}",
                         validation_status="APROBADO", target_database="db_prueba")
                for i in range(3)
            ])
            self.db.commit()

            with patch("app.modules.telemetry_audit.router.EXPORT_MAX_ROWS", 1):
                rows, _ = self._export_rows(marker)

            data_rows = [r for r in rows[1:] if r[0] != "AVISO"]
            self.assertEqual(len(data_rows), 1, "el tope no se aplico al export")

            aviso = next(r for r in rows if r[0] == "AVISO")
            self.assertIn("1 de 3", aviso[-1])
            self.assertIn("truncada", aviso[-1].lower())
        finally:
            self._cleanup_logs(marker)

    def test_export_no_lee_snapshots_de_consultas(self):
        """El SQL del export no puede traer `result_snapshot` de cada fila.

        A ~23 KB promedio y hasta ~108 KB por evento, cargar 50.000 filas con el
        snapshot en memoria ronda 1 GB aunque el CSV no lo escriba. El detalle se
        consulta aparte y solo para `PERMISOS_*`.
        """
        marker = f"export-memoria-{uuid.uuid4().hex[:8]}"
        try:
            self.db.add(AuditLog(
                username=marker,
                question_prompt="consulta con snapshot grande",
                validation_status="APROBADO",
                target_database="db_prueba",
                result_snapshot="x" * 5000,
            ))
            self.db.commit()

            statements = []

            def capture(conn, cursor, statement, parameters, context, executemany):
                statements.append(statement)

            event.listen(engine, "before_cursor_execute", capture)
            try:
                rows, _ = self._export_rows(marker)
            finally:
                event.remove(engine, "before_cursor_execute", capture)

            self.assertEqual(len([r for r in rows[1:] if r[0] != "AVISO"]), 1)
            # El `count()` envuelve la entidad completa en un subquery; no
            # transfiere filas. La consulta que importa es la que trae los datos:
            # la que ordena y aplica el LIMIT.
            fetch = [
                s for s in statements
                if s.lstrip().lower().startswith("select")
                and "audit_logs" in s.lower()
                and " limit " in s.lower()
            ]
            self.assertEqual(len(fetch), 1, "no se capturo la consulta de filas del export")
            self.assertNotIn(
                "result_snapshot", fetch[0].lower(),
                "el export sigue seleccionando el snapshot de todas las filas",
            )
        finally:
            self._cleanup_logs(marker)


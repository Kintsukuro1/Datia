"""Tests del informe BI nativo: gobernanza, contrato, masking y auditoría."""

import unittest
import uuid
from unittest import mock

from fastapi.testclient import TestClient
from sqlalchemy import text

from conftest import (
    create_temp_pg_database,
    drop_temp_pg_database,
    make_auth_headers,
    pg_source_conn_attrs,
    pg_source_engine,
)

from main import app

from app.api.deps import AuditWriteError
from app.core.security import get_password_hash
from app.db.init_db import init_db
from app.modules.admin_catalog.models import (
    ColumnPermissionType,
    CorporateConnection,
    RoleColumnPermission,
    RoleTablePermission,
)
from app.modules.auth.models import Role, User, UserSession
from app.modules.bi.models import BIVisual
from app.modules.chat_engine.dynamic_schema import DynamicSchemaPruningService
from app.modules.telemetry_audit.models import AuditLog

TABLE = "bi_ventas"
URL = "/api/v1/bi"


class TestBIReportApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg_db = create_temp_pg_database("bireport")
        engine = pg_source_engine(cls.pg_db)
        try:
            with engine.begin() as connection:
                connection.execute(text(f"DROP TABLE IF EXISTS {TABLE}"))
                connection.execute(
                    text(
                        f"CREATE TABLE {TABLE} ("
                        "id SERIAL PRIMARY KEY, "
                        "fecha DATE NOT NULL, "
                        "ejecutivo TEXT, "
                        "categoria TEXT, "
                        "monto NUMERIC(12,2), "
                        "cantidad INTEGER, "
                        "rut_cliente TEXT)"
                    )
                )
                connection.execute(
                    text(
                        f"INSERT INTO {TABLE} "
                        "(fecha, ejecutivo, categoria, monto, cantidad, rut_cliente) VALUES "
                        "('2025-01-10', 'Ana', 'Software', 1000, 1, '111'), "
                        "('2025-02-11', 'Luis', 'Servicios', 2000, 2, '222'), "
                        "('2026-01-03', 'Ana', 'Software', 3000, 1, '333'), "
                        "('2026-01-04', 'Luis', 'Servicios', 1000, 2, '444'), "
                        "('2026-01-05', 'Marta', 'Servicios', 500, 3, '555'), "
                        "('2026-01-06', 'Pedro', 'Software', 2500, 4, '666'), "
                        "('2026-02-10', 'Ana', 'Software', 1500, 2, '777'), "
                        "('2026-02-11', 'Luis', 'Servicios', 500, 1, '888'), "
                        "('2026-02-12', 'Marta', 'Software', 1000, 3, '999'), "
                        "('2026-02-13', 'Pedro', 'Servicios', 700, 4, '000')"
                    )
                )
        finally:
            engine.dispose()
        from app.core.database import SessionLocal
        bootstrap = SessionLocal()
        try:
            init_db(bootstrap)
        finally:
            bootstrap.close()
        cls.hashed_password = get_password_hash("BiTest123!")

    @classmethod
    def tearDownClass(cls):
        drop_temp_pg_database(cls.pg_db)

    def setUp(self):
        from app.core.database import SessionLocal

        self.client = TestClient(app)
        self.db = SessionLocal()
        self.suffix = uuid.uuid4().hex[:10]
        self.username = f"bi_user_{self.suffix}"

        self.role = Role(name=f"Rol BI {self.suffix}", description="prueba BI")
        self.db.add(self.role)
        self.db.commit()
        self.db.refresh(self.role)

        self.connection = CorporateConnection(
            name=f"TEST BI {self.suffix}",
            is_active=False,
            is_uploaded=True,
            **pg_source_conn_attrs(self.pg_db),
        )
        self.db.add(self.connection)
        self.db.commit()
        self.db.refresh(self.connection)

        self.db.add(
            RoleTablePermission(
                role_id=self.role.id,
                connection_id=self.connection.id,
                schema_name="public",
                table_name=TABLE,
                is_allowed=True,
                granted_by_admin=True,
            )
        )
        self.db.add_all(
            [
                RoleColumnPermission(
                    role_id=self.role.id,
                    connection_id=self.connection.id,
                    schema_name="public",
                    table_name=TABLE,
                    column_name="monto",
                    permission_type=ColumnPermissionType.MASKED,
                ),
                RoleColumnPermission(
                    role_id=self.role.id,
                    connection_id=self.connection.id,
                    schema_name="public",
                    table_name=TABLE,
                    column_name="rut_cliente",
                    permission_type=ColumnPermissionType.BLOCKED,
                ),
            ]
        )
        self.user = User(
            username=self.username,
            full_name="Usuario BI",
            hashed_password=self.hashed_password,
            role_id=self.role.id,
            is_admin=False,
            is_active=True,
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self.headers = make_auth_headers(self.db, self.username)
        self.connection_id = self.connection.id
        self.user_id = self.user.id
        self.role_id = self.role.id

    def tearDown(self):
        self.db.query(BIVisual).filter(BIVisual.user_id == self.user_id).delete(
            synchronize_session=False
        )
        self.db.query(AuditLog).filter(AuditLog.user_id == self.user_id).delete(
            synchronize_session=False
        )
        self.db.query(UserSession).filter(
            UserSession.user_id == self.user_id
        ).delete(synchronize_session=False)
        self.db.query(User).filter(User.id == self.user_id).delete(
            synchronize_session=False
        )
        self.db.query(RoleColumnPermission).filter(
            RoleColumnPermission.role_id == self.role_id
        ).delete(synchronize_session=False)
        self.db.query(RoleTablePermission).filter(
            RoleTablePermission.role_id == self.role_id
        ).delete(synchronize_session=False)
        self.db.query(CorporateConnection).filter(
            CorporateConnection.id == self.connection_id
        ).delete(synchronize_session=False)
        self.db.query(Role).filter(Role.id == self.role_id).delete(
            synchronize_session=False
        )
        self.db.commit()
        DynamicSchemaPruningService.invalidate_schema_cache(self.connection_id)
        self.db.close()

    def _report(self, **overrides):
        params = {
            "connection_id": self.connection.id,
            "table": TABLE,
        }
        params.update(overrides)
        return self.client.get(
            f"{URL}/report",
            params=params,
            headers=self.headers,
        )

    def test_schema_hides_blocked_and_marks_masked(self):
        response = self.client.get(
            f"{URL}/schema",
            params={"connection_id": self.connection.id},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["is_admin"])
        table = next(t for t in body["tables"] if t["name"] == TABLE)
        names = {column["name"] for column in table["columns"]}
        self.assertNotIn("rut_cliente", names)
        monto = next(column for column in table["columns"] if column["name"] == "monto")
        self.assertTrue(monto["masked"])
        self.assertEqual(monto["kind"], "number")

    def test_connections_only_lists_authorized(self):
        other = CorporateConnection(
            name=f"TEST BI sin permiso {self.suffix}",
            is_active=False,
            is_uploaded=True,
            **pg_source_conn_attrs(self.pg_db),
        )
        self.db.add(other)
        self.db.commit()
        self.db.refresh(other)
        try:
            response = self.client.get(f"{URL}/connections", headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            ids = {item["id"] for item in response.json()}
            self.assertIn(self.connection.id, ids)
            self.assertNotIn(other.id, ids)

            admin_headers = make_auth_headers(self.db, "admin")
            admin_response = self.client.get(
                f"{URL}/connections", headers=admin_headers
            )
            self.assertEqual(admin_response.status_code, 200, admin_response.text)
            admin_ids = {item["id"] for item in admin_response.json()}
            self.assertIn(other.id, admin_ids)
        finally:
            self.db.query(CorporateConnection).filter(
                CorporateConnection.id == other.id
            ).delete(synchronize_session=False)
            self.db.commit()

    def test_report_default_period_kpis_and_masking(self):
        response = self._report(measure="cantidad")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["period"]["year"], 2026)
        self.assertEqual(body["period"]["month"], 2)
        self.assertEqual(body["roles"]["date"], "fecha")
        self.assertEqual(body["roles"]["group"], "ejecutivo")
        self.assertEqual(body["roles"]["category"], "categoria")
        self.assertEqual(body["kpis"]["registros"], 4)
        self.assertEqual(body["kpis"]["total"], 10)
        self.assertEqual(body["kpis"]["grupos"], 4)
        self.assertEqual(body["kpis"]["dias"], 4)
        self.assertEqual(body["kpis"]["promedio_diario"], 2.5)
        self.assertEqual(body["kpis"]["promedio_por_grupo"], 2.5)
        self.assertEqual(len(body["daily"]), 4)
        self.assertEqual(len(body["matrix"]["rows"]), 4)
        self.assertIsNotNone(body["cross"])
        self.assertNotIn("monto", body["detail"]["columns"])
        self.assertNotIn("rut_cliente", body["detail"]["columns"])
        self.assertEqual(len(body["detail"]["rows"]), 4)
        for row in body["detail"]["rows"]:
            self.assertNotIn("monto", row)
            self.assertNotIn("rut_cliente", row)

    def test_report_filters_and_whole_year(self):
        response = self._report(measure="cantidad", month="all")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["kpis"]["registros"], 8)
        self.assertEqual(body["kpis"]["total"], 20)
        self.assertIsNone(body["period"]["month"])
        self.assertIsNone(body["matrix"])

        filtered = self._report(
            measure="cantidad", group_value="Ana", category_value="Software"
        )
        self.assertEqual(filtered.status_code, 200, filtered.text)
        filtered_body = filtered.json()
        self.assertEqual(filtered_body["kpis"]["registros"], 1)
        self.assertEqual(filtered_body["kpis"]["total"], 2)

    def test_report_without_date(self):
        response = self._report(date_column="-", measure="cantidad")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIsNone(body["period"]["year"])
        self.assertEqual(body["period"]["years"], [])
        self.assertIsNone(body["kpis"]["dias"])
        self.assertEqual(body["daily"], [])
        self.assertIsNone(body["matrix"])
        self.assertEqual(body["kpis"]["registros"], 10)
        self.assertEqual(body["kpis"]["total"], 23)

    def test_report_blocks_masked_measure_and_blocked_column(self):
        masked = self._report(measure="monto")
        self.assertEqual(masked.status_code, 403, masked.text)

        blocked = self._report(group_column="rut_cliente", measure="cantidad")
        self.assertEqual(blocked.status_code, 403, blocked.text)

        text_measure = self._report(measure="ejecutivo")
        self.assertEqual(text_measure.status_code, 400, text_measure.text)

    def test_report_rejects_invalid_table_year_and_month(self):
        missing = self._report(table="no_existe")
        self.assertEqual(missing.status_code, 404, missing.text)

        year = self._report(measure="cantidad", year=2024)
        self.assertEqual(year.status_code, 400, year.text)

        month = self._report(measure="cantidad", year=2026, month=13)
        self.assertEqual(month.status_code, 400, month.text)

    def test_report_escapes_filter_values(self):
        response = self._report(
            measure="cantidad",
            group_value="' OR 1=1 --",
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["kpis"]["registros"], 0)

    def test_report_audits_success_and_rejection(self):
        response = self._report(measure="cantidad")
        self.assertEqual(response.status_code, 200, response.text)
        audit = (
            self.db.query(AuditLog)
            .filter(AuditLog.user_id == self.user.id)
            .order_by(AuditLog.id.desc())
            .first()
        )
        self.assertIsNotNone(audit)
        self.assertEqual(audit.validation_status, "APROBADO")
        self.assertIn("Informe BI", audit.question_prompt)
        self.assertEqual(audit.target_database, self.connection.name)
        self.assertEqual(audit.rows_returned, 4)
        self.assertTrue(audit.sql_generated)
        self.assertIn("SELECT", audit.sql_generated.upper())

        rejected = self._report(measure="monto")
        self.assertEqual(rejected.status_code, 403)
        rejection = (
            self.db.query(AuditLog)
            .filter(AuditLog.user_id == self.user.id)
            .order_by(AuditLog.id.desc())
            .first()
        )
        self.assertEqual(rejection.validation_status, "RECHAZADO_RBAC")

    def test_admin_can_use_masked_measure_and_see_detail(self):
        admin_headers = make_auth_headers(self.db, "admin")
        schema = self.client.get(
            f"{URL}/schema",
            params={"connection_id": self.connection.id},
            headers=admin_headers,
        )
        self.assertEqual(schema.status_code, 200, schema.text)
        table = next(t for t in schema.json()["tables"] if t["name"] == TABLE)
        monto = next(column for column in table["columns"] if column["name"] == "monto")
        self.assertFalse(monto["masked"])

        response = self.client.get(
            f"{URL}/report",
            params={
                "connection_id": self.connection.id,
                "table": TABLE,
                "measure": "monto",
            },
            headers=admin_headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["kpis"]["registros"], 4)
        self.assertEqual(body["kpis"]["total"], 3700)
        self.assertIn("monto", body["detail"]["columns"])
        self.assertIn("rut_cliente", body["detail"]["columns"])

    def test_report_delta_against_previous_period(self):
        # Año 2026 completo: 20 vs 3 del año 2025 => +566,67 %.
        whole_year = self._report(measure="cantidad", month="all")
        self.assertEqual(whole_year.status_code, 200, whole_year.text)
        body = whole_year.json()
        self.assertEqual(body["kpis"]["total"], 20)
        self.assertEqual(body["kpis"]["total_anterior"], 3)
        self.assertEqual(body["kpis"]["delta_pct"], 566.67)

        # Febrero 2026 (default): 10 vs 10 de enero => sin variación.
        default = self._report(measure="cantidad")
        self.assertEqual(default.status_code, 200, default.text)
        default_body = default.json()
        self.assertEqual(default_body["kpis"]["total_anterior"], 10)
        self.assertEqual(default_body["kpis"]["delta_pct"], 0.0)

    def test_report_without_date_has_no_delta(self):
        response = self._report(date_column="-", measure="cantidad")
        self.assertEqual(response.status_code, 200, response.text)
        kpis = response.json()["kpis"]
        self.assertIsNone(kpis["total_anterior"])
        self.assertIsNone(kpis["delta_pct"])

    def test_report_without_role_is_denied_and_audited(self):
        username = f"bi_norole_{self.suffix}"
        user = User(
            username=username,
            full_name="Sin rol",
            hashed_password=self.hashed_password,
            role_id=None,
            is_admin=False,
            is_active=True,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        headers = make_auth_headers(self.db, username)
        try:
            response = self.client.get(f"{URL}/connections", headers=headers)
            self.assertEqual(response.status_code, 403, response.text)
            audit = (
                self.db.query(AuditLog)
                .filter(AuditLog.user_id == user.id)
                .order_by(AuditLog.id.desc())
                .first()
            )
            self.assertIsNotNone(audit)
            self.assertEqual(audit.validation_status, "RECHAZADO_RBAC")
            self.assertIsNone(audit.user_role)
        finally:
            self.db.query(AuditLog).filter(AuditLog.user_id == user.id).delete(
                synchronize_session=False
            )
            self.db.query(UserSession).filter(
                UserSession.user_id == user.id
            ).delete(synchronize_session=False)
            self.db.query(User).filter(User.id == user.id).delete(
                synchronize_session=False
            )
            self.db.commit()

    def test_audit_failure_prevents_data(self):
        with mock.patch(
            "app.modules.bi.report_service._persist_audit_log",
            side_effect=AuditWriteError("sin auditoría"),
        ):
            response = self._report(measure="cantidad")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertNotIn("kpis", response.json())


class TestBIPivotHelpers(unittest.TestCase):
    def test_cross_collapses_extra_categories_into_otros(self):
        from app.modules.bi.report_service import _pivot_cross

        top = ["A", "B"]
        rows = [
            {"g": "Ana", "c": "A", "value": 2},
            {"g": "Ana", "c": "C", "value": 3},
            {"g": "Luis", "c": "B", "value": 4},
        ]
        cross = _pivot_cross(rows, top)
        self.assertEqual(cross.categories, ["A", "B", "Otros"])
        ana = next(row for row in cross.rows if row.label == "Ana")
        self.assertEqual(ana.cells["Otros"], 3)
        self.assertEqual(ana.total, 5)
        self.assertEqual(ana.total, sum(ana.cells.values()))

    def test_matrix_keeps_missing_cells_missing(self):
        from app.modules.bi.report_service import _pivot_matrix

        matrix = _pivot_matrix(
            [
                {"g": "Ana", "d": "2026-01-02", "value": 2},
                {"g": "Luis", "d": "2026-01-01", "value": 4},
            ]
        )
        self.assertEqual(matrix.days, ["2026-01-01", "2026-01-02"])
        ana = next(row for row in matrix.rows if row.label == "Ana")
        self.assertNotIn("2026-01-01", ana.cells)
        self.assertEqual(ana.total, 2)

"""Tests de las pestañas del módulo BI: renombrar, borrar y filtros compartidos."""

import unittest
import uuid

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

from app.core.security import get_password_hash
from app.db.init_db import init_db
from app.modules.admin_catalog.models import (
    ColumnPermissionType,
    CorporateConnection,
    RoleColumnPermission,
    RoleTablePermission,
)
from app.modules.auth.models import Role, User, UserSession
from app.modules.bi.models import BIPanel, BIVisual
from app.modules.chat_engine.dynamic_schema import DynamicSchemaPruningService
from app.modules.telemetry_audit.models import AuditLog

TABLE = "bi_panels_test"
URL = "/api/v1/bi"


class TestBIPanelsApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg_db = create_temp_pg_database("bipanels")
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
        cls.hashed_password = get_password_hash("BiPanel123!")

    @classmethod
    def tearDownClass(cls):
        drop_temp_pg_database(cls.pg_db)

    def setUp(self):
        from app.core.database import SessionLocal

        self.client = TestClient(app)
        self.db = SessionLocal()
        self.suffix = uuid.uuid4().hex[:10]
        self.username = f"bi_panel_{self.suffix}"

        self.role = Role(name=f"Rol BI panel {self.suffix}", description="prueba")
        self.db.add(self.role)
        self.db.commit()
        self.db.refresh(self.role)

        self.connection = CorporateConnection(
            name=f"TEST BI panel {self.suffix}",
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
            full_name="Usuario panel",
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
        self.db.query(BIPanel).filter(BIPanel.user_id == self.user_id).delete(
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

    def _spec(self, **overrides):
        spec = {
            "connection_id": self.connection.id,
            "table": TABLE,
            "chart_type": "bar",
            "dimension": "categoria",
            "date_grain": None,
            "measure": "cantidad",
            "aggregation": "sum",
            "filters": [],
            "sort": "value_desc",
            "limit": 20,
        }
        spec.update(overrides)
        return spec

    def _create_visual(self, panel_name="Ganancias"):
        response = self.client.post(
            f"{URL}/visuals",
            json={
                "title": "Cantidad por categoría",
                "panel_name": panel_name,
                "spec": self._spec(),
            },
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _put_filters(self, panel_name, filters):
        return self.client.put(
            f"{URL}/panels/filters",
            json={"panel_name": panel_name, "filters": filters},
            headers=self.headers,
        )

    def _panels(self):
        response = self.client.get(f"{URL}/panels", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return {panel["name"]: panel["filters"] for panel in response.json()}

    def test_list_panels_unions_explicit_rows_and_visual_names(self):
        self._create_visual(panel_name="Ganancias")
        put = self._put_filters("Extra", [
            {
                "connection_id": self.connection.id,
                "table": TABLE,
                "column": "fecha",
                "op": ">=",
                "value": "2026-02-01",
            }
        ])
        self.assertEqual(put.status_code, 200, put.text)

        panels = self._panels()
        self.assertIn("Ganancias", panels)
        self.assertIn("Extra", panels)
        self.assertEqual(panels["Ganancias"], [])
        self.assertEqual(len(panels["Extra"]), 1)

    def test_rename_moves_visuals_and_keeps_filters(self):
        self._create_visual(panel_name="Ganancias")
        self._put_filters("Ganancias", [
            {
                "connection_id": self.connection.id,
                "table": TABLE,
                "column": "fecha",
                "op": ">=",
                "value": "2026-02-01",
            }
        ])

        renamed = self.client.patch(
            f"{URL}/panels/rename",
            json={"panel_name": "Ganancias", "new_name": "Ganancias 2026"},
            headers=self.headers,
        )
        self.assertEqual(renamed.status_code, 200, renamed.text)

        panels = self._panels()
        self.assertNotIn("Ganancias", panels)
        self.assertEqual(len(panels["Ganancias 2026"]), 1)

        listed = self.client.get(f"{URL}/visuals", headers=self.headers)
        visual = listed.json()[0]
        self.assertEqual(visual["panel_name"], "Ganancias 2026")

    def test_rename_rejects_conflicting_name(self):
        self._create_visual(panel_name="Panel A")
        self._create_visual(panel_name="Panel B")

        conflict = self.client.patch(
            f"{URL}/panels/rename",
            json={"panel_name": "Panel A", "new_name": "Panel B"},
            headers=self.headers,
        )
        self.assertEqual(conflict.status_code, 400, conflict.text)

        # El panel original queda intacto tras el rechazo.
        listed = self.client.get(f"{URL}/visuals", headers=self.headers)
        names = {visual["panel_name"] for visual in listed.json()}
        self.assertEqual(names, {"Panel A", "Panel B"})

    def test_rename_missing_panel_returns_404(self):
        response = self.client.patch(
            f"{URL}/panels/rename",
            json={"panel_name": "Inexistente", "new_name": "Otro"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_delete_panel_removes_visuals_and_filters(self):
        visual = self._create_visual(panel_name="Ganancias")
        self._put_filters("Ganancias", [
            {
                "connection_id": self.connection.id,
                "table": TABLE,
                "column": "fecha",
                "op": ">=",
                "value": "2026-02-01",
            }
        ])

        deleted = self.client.delete(
            f"{URL}/panels/Ganancias", headers=self.headers
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertNotIn("Ganancias", self._panels())

        listed = self.client.get(f"{URL}/visuals", headers=self.headers)
        self.assertEqual(listed.json(), [])

        # El visual ya no existe: el borrado del panel lo llevó.
        gone = self.client.post(
            f"{URL}/visuals/{visual['id']}/data", headers=self.headers
        )
        self.assertEqual(gone.status_code, 404, gone.text)

        again = self.client.delete(
            f"{URL}/panels/Ganancias", headers=self.headers
        )
        self.assertEqual(again.status_code, 404, again.text)

    def test_panel_filter_applies_to_visual_data_but_not_preview(self):
        visual = self._create_visual(panel_name="Ganancias")
        put = self._put_filters("Ganancias", [
            {
                "connection_id": self.connection.id,
                "table": TABLE,
                "column": "fecha",
                "op": ">=",
                "value": "2026-02-01",
            }
        ])
        self.assertEqual(put.status_code, 200, put.text)

        data = self.client.post(
            f"{URL}/visuals/{visual['id']}/data", headers=self.headers
        )
        self.assertEqual(data.status_code, 200, data.text)
        values = {row["label"]: row["value"] for row in data.json()["rows"]}
        self.assertEqual(values, {"Software": 5, "Servicios": 5})
        # El SQL auditado incluye la condición del panel.
        self.assertIn("fecha", data.json()["sql"])

        preview = self.client.post(
            f"{URL}/query", json=self._spec(), headers=self.headers
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        preview_values = {
            row["label"]: row["value"] for row in preview.json()["rows"]
        }
        self.assertEqual(preview_values, {"Software": 10, "Servicios": 10})

    def test_panel_filter_on_masked_column_fails_closed(self):
        visual = self._create_visual(panel_name="Ganancias")
        put = self._put_filters("Ganancias", [
            {
                "connection_id": self.connection.id,
                "table": TABLE,
                "column": "monto",
                "op": ">=",
                "value": 100,
            }
        ])
        self.assertEqual(put.status_code, 200, put.text)

        denied = self.client.post(
            f"{URL}/visuals/{visual['id']}/data", headers=self.headers
        )
        self.assertEqual(denied.status_code, 403, denied.text)

        rechazados = (
            self.db.query(AuditLog)
            .filter(
                AuditLog.user_id == self.user_id,
                AuditLog.validation_status == "RECHAZADO_RBAC",
            )
            .count()
        )
        self.assertGreaterEqual(rechazados, 1)

    def test_panel_filter_ignores_other_table_or_connection(self):
        visual = self._create_visual(panel_name="Ganancias")
        put = self._put_filters("Ganancias", [
            {
                "connection_id": self.connection.id + 999,
                "table": TABLE,
                "column": "fecha",
                "op": ">=",
                "value": "2026-02-01",
            },
            {
                "connection_id": self.connection.id,
                "table": "otra_tabla",
                "column": "fecha",
                "op": ">=",
                "value": "2026-02-01",
            },
        ])
        self.assertEqual(put.status_code, 200, put.text)

        data = self.client.post(
            f"{URL}/visuals/{visual['id']}/data", headers=self.headers
        )
        self.assertEqual(data.status_code, 200, data.text)
        values = {row["label"]: row["value"] for row in data.json()["rows"]}
        self.assertEqual(values, {"Software": 10, "Servicios": 10})

    def test_panel_filter_with_empty_value_is_ignored(self):
        visual = self._create_visual(panel_name="Ganancias")
        self._put_filters("Ganancias", [
            {
                "connection_id": self.connection.id,
                "table": TABLE,
                "column": "fecha",
                "op": ">=",
                "value": "",
            }
        ])

        data = self.client.post(
            f"{URL}/visuals/{visual['id']}/data", headers=self.headers
        )
        self.assertEqual(data.status_code, 200, data.text)
        values = {row["label"]: row["value"] for row in data.json()["rows"]}
        self.assertEqual(values, {"Software": 10, "Servicios": 10})

    def test_panel_filters_reject_more_than_five(self):
        filters = [
            {
                "connection_id": self.connection.id,
                "table": TABLE,
                "column": "categoria",
                "op": "=",
                "value": f"valor {index}",
            }
            for index in range(6)
        ]
        response = self._put_filters("Ganancias", filters)
        self.assertEqual(response.status_code, 422, response.text)

    def test_other_user_cannot_rename_or_delete_my_panel(self):
        self._create_visual(panel_name="Ganancias")

        other_username = f"bi_panel_other_{self.suffix}"
        other = User(
            username=other_username,
            full_name="Otro usuario panel",
            hashed_password=self.hashed_password,
            role_id=self.role.id,
            is_admin=False,
            is_active=True,
        )
        self.db.add(other)
        self.db.commit()
        self.db.refresh(other)
        other_headers = make_auth_headers(self.db, other_username)
        try:
            renamed = self.client.patch(
                f"{URL}/panels/rename",
                json={"panel_name": "Ganancias", "new_name": "Suyo"},
                headers=other_headers,
            )
            self.assertEqual(renamed.status_code, 404, renamed.text)

            deleted = self.client.delete(
                f"{URL}/panels/Ganancias", headers=other_headers
            )
            self.assertEqual(deleted.status_code, 404, deleted.text)

            listed = self.client.get(f"{URL}/visuals", headers=self.headers)
            self.assertEqual(len(listed.json()), 1)
        finally:
            self.db.query(UserSession).filter(
                UserSession.user_id == other.id
            ).delete(synchronize_session=False)
            self.db.query(User).filter(User.id == other.id).delete(
                synchronize_session=False
            )
            self.db.commit()


if __name__ == "__main__":
    unittest.main()

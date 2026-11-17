"""Tests de los visuales guardados del módulo BI."""

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
from app.modules.bi.models import BIVisual
from app.modules.chat_engine.dynamic_schema import DynamicSchemaPruningService
from app.modules.telemetry_audit.models import AuditLog

TABLE = "bi_ventas_visual"
URL = "/api/v1/bi"


class TestBIVisualsApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg_db = create_temp_pg_database("bivisual")
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
        cls.hashed_password = get_password_hash("BiTest123!")

    @classmethod
    def tearDownClass(cls):
        drop_temp_pg_database(cls.pg_db)

    def setUp(self):
        from app.core.database import SessionLocal

        self.client = TestClient(app)
        self.db = SessionLocal()
        self.suffix = uuid.uuid4().hex[:10]
        self.username = f"bi_visual_{self.suffix}"

        self.role = Role(name=f"Rol BI visual {self.suffix}", description="prueba")
        self.db.add(self.role)
        self.db.commit()
        self.db.refresh(self.role)

        self.connection = CorporateConnection(
            name=f"TEST BI visual {self.suffix}",
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
            full_name="Usuario visual",
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

    def test_preview_returns_aggregated_rows_and_audits(self):
        response = self.client.post(
            f"{URL}/query", json=self._spec(), headers=self.headers
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(len(body["rows"]), 2)
        self.assertIn("SELECT", body["sql"].upper())
        self.assertFalse(body["masked_label"])
        self.assertIsNotNone(body["audit_log_id"])
        by_label = {row["label"]: row["value"] for row in body["rows"]}
        self.assertEqual(by_label["Software"], 10)
        self.assertEqual(by_label["Servicios"], 10)

    def test_preview_blocks_masked_aggregation_but_allows_count(self):
        blocked = self.client.post(
            f"{URL}/query",
            json=self._spec(measure="monto", aggregation="sum"),
            headers=self.headers,
        )
        self.assertEqual(blocked.status_code, 403, blocked.text)

        counted = self.client.post(
            f"{URL}/query",
            json=self._spec(measure="monto", aggregation="count"),
            headers=self.headers,
        )
        self.assertEqual(counted.status_code, 200, counted.text)
        counted_rows = counted.json()["rows"]
        self.assertTrue(counted_rows)
        self.assertTrue(all(isinstance(row["value"], (int, float)) for row in counted_rows))

    def test_preview_blocks_filter_on_blocked_column(self):
        response = self.client.post(
            f"{URL}/query",
            json=self._spec(
                filters=[{"column": "rut_cliente", "op": "=", "value": "333"}]
            ),
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 403, response.text)

    def test_masked_dimension_returns_masked_labels(self):
        response = self.client.post(
            f"{URL}/query",
            json=self._spec(dimension="monto", measure=None, aggregation="count"),
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["masked_label"])
        self.assertTrue(all("*" in str(row["label"]) for row in body["rows"]))

    def test_visual_crud_and_ownership(self):
        created = self.client.post(
            f"{URL}/visuals",
            json={
                "title": "Cantidad por categoría",
                "panel_name": "Ventas",
                "spec": self._spec(),
            },
            headers=self.headers,
        )
        self.assertEqual(created.status_code, 201, created.text)
        visual = created.json()
        self.assertEqual(visual["title"], "Cantidad por categoría")
        self.assertIsNotNone(visual["spec"])

        listed = self.client.get(f"{URL}/visuals", headers=self.headers)
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(len(listed.json()), 1)

        data = self.client.post(
            f"{URL}/visuals/{visual['id']}/data", headers=self.headers
        )
        self.assertEqual(data.status_code, 200, data.text)
        self.assertEqual(len(data.json()["rows"]), 2)

        other_username = f"bi_other_{self.suffix}"
        other = User(
            username=other_username,
            full_name="Otro usuario",
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
            hidden = self.client.get(f"{URL}/visuals", headers=other_headers)
            self.assertEqual(hidden.status_code, 200)
            self.assertEqual(hidden.json(), [])

            not_owned = self.client.post(
                f"{URL}/visuals/{visual['id']}/data", headers=other_headers
            )
            self.assertEqual(not_owned.status_code, 404)

            not_owned_delete = self.client.delete(
                f"{URL}/visuals/{visual['id']}", headers=other_headers
            )
            self.assertEqual(not_owned_delete.status_code, 404)
        finally:
            self.db.query(BIVisual).filter(BIVisual.user_id == other.id).delete(
                synchronize_session=False
            )
            self.db.query(AuditLog).filter(AuditLog.user_id == other.id).delete(
                synchronize_session=False
            )
            self.db.query(UserSession).filter(
                UserSession.user_id == other.id
            ).delete(synchronize_session=False)
            self.db.query(User).filter(User.id == other.id).delete(
                synchronize_session=False
            )
            self.db.commit()

        deleted = self.client.delete(
            f"{URL}/visuals/{visual['id']}", headers=self.headers
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(
            self.client.get(f"{URL}/visuals", headers=self.headers).json(), []
        )

    def test_update_visual_changes_title_and_spec(self):
        created = self.client.post(
            f"{URL}/visuals",
            json={
                "title": "Original",
                "panel_name": "Edición",
                "spec": self._spec(),
            },
            headers=self.headers,
        )
        self.assertEqual(created.status_code, 201, created.text)
        visual = created.json()

        updated = self.client.put(
            f"{URL}/visuals/{visual['id']}",
            json={
                "title": "Editado",
                "panel_name": "Edición",
                "spec": self._spec(dimension="ejecutivo"),
            },
            headers=self.headers,
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        body = updated.json()
        self.assertEqual(body["title"], "Editado")
        self.assertEqual(body["spec"]["dimension"], "ejecutivo")

        listed = self.client.get(
            f"{URL}/visuals", params={"panel_name": "Edición"}, headers=self.headers
        )
        self.assertEqual(listed.status_code, 200, listed.text)
        saved = next(
            item for item in listed.json() if item["id"] == visual["id"]
        )
        self.assertEqual(saved["title"], "Editado")

        data = self.client.post(
            f"{URL}/visuals/{visual['id']}/data", headers=self.headers
        )
        self.assertEqual(data.status_code, 200, data.text)
        # 4 ejecutivos en los datos de prueba => 4 grupos.
        self.assertEqual(len(data.json()["rows"]), 4)

    def test_update_rejects_invalid_spec_and_keeps_original(self):
        created = self.client.post(
            f"{URL}/visuals",
            json={
                "title": "Original",
                "panel_name": "Edición",
                "spec": self._spec(),
            },
            headers=self.headers,
        )
        self.assertEqual(created.status_code, 201, created.text)
        visual = created.json()

        invalid = self.client.put(
            f"{URL}/visuals/{visual['id']}",
            json={
                "title": "Roto",
                "panel_name": "Edición",
                "spec": self._spec(table="no_existe"),
            },
            headers=self.headers,
        )
        self.assertEqual(invalid.status_code, 404, invalid.text)

        listed = self.client.get(
            f"{URL}/visuals", params={"panel_name": "Edición"}, headers=self.headers
        )
        saved = next(
            item for item in listed.json() if item["id"] == visual["id"]
        )
        self.assertEqual(saved["title"], "Original")
        self.assertEqual(saved["spec"]["table"], TABLE)

    def test_update_of_other_users_visual_is_404(self):
        created = self.client.post(
            f"{URL}/visuals",
            json={
                "title": "Mío",
                "panel_name": "Edición",
                "spec": self._spec(),
            },
            headers=self.headers,
        )
        self.assertEqual(created.status_code, 201, created.text)
        visual = created.json()

        other_username = f"bi_editor_{self.suffix}"
        other = User(
            username=other_username,
            full_name="Otro editor",
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
            response = self.client.put(
                f"{URL}/visuals/{visual['id']}",
                json={
                    "title": "Hackeado",
                    "panel_name": "Edición",
                    "spec": self._spec(),
                },
                headers=other_headers,
            )
            self.assertEqual(response.status_code, 404, response.text)
        finally:
            self.db.query(BIVisual).filter(BIVisual.user_id == other.id).delete(
                synchronize_session=False
            )
            self.db.query(AuditLog).filter(AuditLog.user_id == other.id).delete(
                synchronize_session=False
            )
            self.db.query(UserSession).filter(
                UserSession.user_id == other.id
            ).delete(synchronize_session=False)
            self.db.query(User).filter(User.id == other.id).delete(
                synchronize_session=False
            )
            self.db.commit()

    def test_reorder_visuals_persists_order(self):
        ids = []
        for name in ("Primero", "Segundo", "Tercero"):
            created = self.client.post(
                f"{URL}/visuals",
                json={
                    "title": name,
                    "panel_name": "Orden",
                    "spec": self._spec(dimension="ejecutivo"),
                },
                headers=self.headers,
            )
            self.assertEqual(created.status_code, 201, created.text)
            ids.append(created.json()["id"])

        reordered = self.client.put(
            f"{URL}/visuals/positions",
            json={"panel_name": "Orden", "ids": list(reversed(ids))},
            headers=self.headers,
        )
        self.assertEqual(reordered.status_code, 200, reordered.text)
        self.assertEqual(
            [visual["id"] for visual in reordered.json()],
            list(reversed(ids)),
        )

        listed = self.client.get(
            f"{URL}/visuals", params={"panel_name": "Orden"}, headers=self.headers
        )
        self.assertEqual(
            [visual["title"] for visual in listed.json()],
            ["Tercero", "Segundo", "Primero"],
        )

    def test_reorder_rejects_foreign_incomplete_or_duplicated(self):
        ids = []
        for name in ("A", "B"):
            created = self.client.post(
                f"{URL}/visuals",
                json={
                    "title": name,
                    "panel_name": "Errores",
                    "spec": self._spec(),
                },
                headers=self.headers,
            )
            self.assertEqual(created.status_code, 201, created.text)
            ids.append(created.json()["id"])

        foreign = self.client.put(
            f"{URL}/visuals/positions",
            json={"panel_name": "Errores", "ids": [ids[0], 999999]},
            headers=self.headers,
        )
        self.assertEqual(foreign.status_code, 404, foreign.text)

        incomplete = self.client.put(
            f"{URL}/visuals/positions",
            json={"panel_name": "Errores", "ids": [ids[0]]},
            headers=self.headers,
        )
        self.assertEqual(incomplete.status_code, 400, incomplete.text)

        duplicated = self.client.put(
            f"{URL}/visuals/positions",
            json={"panel_name": "Errores", "ids": [ids[0], ids[0], ids[1]]},
            headers=self.headers,
        )
        self.assertEqual(duplicated.status_code, 400, duplicated.text)

    def test_visual_size_roundtrip_and_default(self):
        created = self.client.post(
            f"{URL}/visuals",
            json={
                "title": "Ancho",
                "panel_name": "Tamaños",
                "spec": self._spec(size="wide"),
            },
            headers=self.headers,
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["spec"]["size"], "wide")

        # Sin size: default "small" (visuales guardados antes del campo).
        legacy = self.client.post(
            f"{URL}/visuals",
            json={
                "title": "Legado",
                "panel_name": "Tamaños",
                "spec": self._spec(),
            },
            headers=self.headers,
        )
        self.assertEqual(legacy.status_code, 201, legacy.text)
        self.assertEqual(legacy.json()["spec"]["size"], "small")

        updated = self.client.put(
            f"{URL}/visuals/{legacy.json()['id']}",
            json={
                "title": "Legado",
                "panel_name": "Tamaños",
                "spec": self._spec(size="full"),
            },
            headers=self.headers,
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["spec"]["size"], "full")

    def test_create_rejects_invalid_table(self):
        response = self.client.post(
            f"{URL}/visuals",
            json={
                "title": "Inválido",
                "panel_name": "Ventas",
                "spec": self._spec(table="no_existe"),
            },
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 404, response.text)

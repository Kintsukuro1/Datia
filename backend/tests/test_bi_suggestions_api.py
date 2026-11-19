"""Tests de las sugerencias de visuales con la IA local (LLM mockeado).

La IA no es parte de la verificación: se mockea para devolver un JSON
controlado y se prueba la VALIDACIÓN server-side (columnas que no existen,
columnas enmascaradas, enums inválidos) y los 503 honestos.
"""

import unittest
import uuid
from unittest.mock import AsyncMock, patch

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
from app.modules.chat_engine.dynamic_schema import DynamicSchemaPruningService
from app.modules.chat_engine.llm_service import LLMService
from app.modules.telemetry_audit.models import AuditLog

TABLE = "bi_suggest_test"
URL = "/api/v1/bi"


def _llm_payload() -> str:
    """Dos sugerencias válidas y dos inválidas, en el orden en que llegan."""
    return """[
      {"title": "Cantidad por categoría", "chart_type": "bar",
       "dimension": "categoria", "date_grain": null, "measure": "cantidad",
       "aggregation": "sum", "series": null, "filters": [],
       "sort": "value_desc", "limit": 20},
      {"title": "Evolución mensual", "chart_type": "line",
       "dimension": "fecha", "date_grain": "month", "measure": null,
       "aggregation": "count", "series": null, "filters": [],
       "sort": "label_asc", "limit": 20},
      {"title": "Columna inventada", "chart_type": "bar",
       "dimension": "no_existe", "date_grain": null, "measure": null,
       "aggregation": "count", "series": null, "filters": [],
       "sort": "value_desc", "limit": 20},
      {"title": "Filtro confidencial", "chart_type": "bar",
       "dimension": "categoria", "date_grain": null, "measure": null,
       "aggregation": "count", "series": null,
       "filters": [{"column": "monto", "op": ">=", "value": 100}],
       "sort": "value_desc", "limit": 20}
    ]"""


class TestBISuggestionsApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg_db = create_temp_pg_database("bisuggest")
        engine = pg_source_engine(cls.pg_db)
        try:
            with engine.begin() as connection:
                connection.execute(text(f"DROP TABLE IF EXISTS {TABLE}"))
                connection.execute(
                    text(
                        f"CREATE TABLE {TABLE} ("
                        "id SERIAL PRIMARY KEY, "
                        "fecha DATE NOT NULL, "
                        "categoria TEXT, "
                        "monto NUMERIC(12,2), "
                        "cantidad INTEGER)"
                    )
                )
                connection.execute(
                    text(
                        f"INSERT INTO {TABLE} (fecha, categoria, monto, cantidad) VALUES "
                        "('2026-01-03', 'Software', 3000, 1), "
                        "('2026-02-04', 'Servicios', 1000, 2)"
                    )
                )
                # Tabla física SIN permiso para el rol: existe en la base,
                # pero el contexto autorizado no la incluye.
                connection.execute(text("DROP TABLE IF EXISTS bi_suggest_secret"))
                connection.execute(
                    text("CREATE TABLE bi_suggest_secret (id SERIAL PRIMARY KEY, dato TEXT)")
                )
        finally:
            engine.dispose()
        from app.core.database import SessionLocal
        bootstrap = SessionLocal()
        try:
            init_db(bootstrap)
        finally:
            bootstrap.close()
        cls.hashed_password = get_password_hash("BiSuggest123!")

    @classmethod
    def tearDownClass(cls):
        drop_temp_pg_database(cls.pg_db)

    def setUp(self):
        from app.core.database import SessionLocal

        self.client = TestClient(app)
        self.db = SessionLocal()
        self.suffix = uuid.uuid4().hex[:10]
        self.username = f"bi_suggest_{self.suffix}"

        self.role = Role(name=f"Rol BI suggest {self.suffix}", description="prueba")
        self.db.add(self.role)
        self.db.commit()
        self.db.refresh(self.role)

        self.connection = CorporateConnection(
            name=f"TEST BI suggest {self.suffix}",
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
        self.db.add(
            RoleColumnPermission(
                role_id=self.role.id,
                connection_id=self.connection.id,
                schema_name="public",
                table_name=TABLE,
                column_name="monto",
                permission_type=ColumnPermissionType.MASKED,
            )
        )
        self.user = User(
            username=self.username,
            full_name="Usuario sugerencias",
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

    def _suggest(self):
        return self.client.post(
            f"{URL}/suggest-visuals",
            json={"connection_id": self.connection.id, "table": TABLE},
            headers=self.headers,
        )

    def test_valid_suggestions_pass_and_invalid_are_discarded(self):
        with patch.object(
            LLMService,
            "generate_completion",
            new=AsyncMock(return_value=_llm_payload()),
        ):
            response = self._suggest()
        self.assertEqual(response.status_code, 200, response.text)

        suggestions = response.json()["suggestions"]
        titles = {item["title"] for item in suggestions}
        # Las dos válidas pasan; la columna inventada y el filtro sobre la
        # columna enmascarada se descartan.
        self.assertEqual(
            titles, {"Cantidad por categoría", "Evolución mensual"}
        )
        for item in suggestions:
            spec = item["spec"]
            self.assertEqual(spec["connection_id"], self.connection.id)
            self.assertEqual(spec["table"], TABLE)

    def test_llm_down_returns_503_without_suggestions(self):
        with patch.object(
            LLMService,
            "generate_completion",
            new=AsyncMock(side_effect=ConnectionError("sin servidor")),
        ):
            response = self._suggest()
        self.assertEqual(response.status_code, 503, response.text)
        self.assertNotIn("suggestions", response.json())

    def test_non_json_response_returns_503(self):
        with patch.object(
            LLMService,
            "generate_completion",
            new=AsyncMock(return_value="Creo que un gráfico de barras estaría bien."),
        ):
            response = self._suggest()
        self.assertEqual(response.status_code, 503, response.text)

    def test_unauthorized_table_is_rejected_before_the_llm(self):
        called = AsyncMock(return_value=_llm_payload())
        with patch.object(LLMService, "generate_completion", new=called):
            response = self.client.post(
                f"{URL}/suggest-visuals",
                json={"connection_id": self.connection.id, "table": "bi_suggest_secret"},
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 403, response.text)
        called.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

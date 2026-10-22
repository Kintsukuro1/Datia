"""
La migracion que abre `audit_logs.validation_status` a NULL.

Corre sobre una base PostgreSQL desechable de verdad: se construye la tabla con
el DDL viejo (NOT NULL, como la de un despliegue ya existente), se la migra, y
se verifica que los datos que ya estaban siguen intactos. Esto es lo que un
`ALTER` a mano sobre el modelo no cubre: el modelo solo decide como se crea la
tabla nueva.
"""
import unittest

from sqlalchemy import inspect, text

from conftest import create_temp_pg_database, drop_temp_pg_database, pg_source_engine


LEGACY_DDL = """CREATE TABLE audit_logs (
    id INTEGER NOT NULL,
    timestamp TIMESTAMP,
    user_id INTEGER,
    username VARCHAR(50) NOT NULL,
    user_role VARCHAR(50),
    question_prompt TEXT NOT NULL,
    sql_generated TEXT,
    validation_status VARCHAR(50) NOT NULL,
    target_database VARCHAR(100),
    execution_time_ms INTEGER,
    rows_returned INTEGER,
    error_message TEXT,
    result_snapshot TEXT,
    PRIMARY KEY (id)
)"""


class TestValidationStatusMigration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.pg_db = create_temp_pg_database("vsmig")

    @classmethod
    def tearDownClass(cls):
        drop_temp_pg_database(cls.pg_db)

    def setUp(self):
        self.engine = pg_source_engine(self.pg_db)
        with self.engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS audit_logs"))

    def tearDown(self):
        self.engine.dispose()

    def _legacy_db(self):
        """Base con el esquema de un despliegue previo (NOT NULL) y datos en ella."""
        with self.engine.begin() as conn:
            conn.execute(text(LEGACY_DDL))
            conn.execute(text("CREATE INDEX ix_audit_logs_id ON audit_logs (id)"))
            conn.execute(text("CREATE INDEX ix_audit_logs_timestamp ON audit_logs (timestamp)"))
            conn.execute(text(
                "INSERT INTO audit_logs (id, timestamp, username, question_prompt, validation_status) "
                "VALUES (1, '2024-01-01 00:00:00', 'admin', 'consulta vieja', 'APROBADO')"
            ))
            conn.execute(text(
                "INSERT INTO audit_logs (id, timestamp, username, question_prompt, validation_status) "
                "VALUES (2, '2024-01-02 00:00:00', 'ti', 'consulta rechazada', 'RECHAZADO_RBAC')"
            ))
        return self.engine

    def _migrate(self, engine):
        from app.core.database import ensure_schema_migrations
        ensure_schema_migrations(engine)

    def test_migration_opens_the_column_and_keeps_the_data(self):
        engine = self._legacy_db()

        before = {c["name"]: c for c in inspect(engine).get_columns("audit_logs")}
        self.assertFalse(before["validation_status"]["nullable"], "el DDL de prueba no es NOT NULL")

        self._migrate(engine)

        after = {c["name"]: c for c in inspect(engine).get_columns("audit_logs")}
        self.assertTrue(after["validation_status"]["nullable"], "la migracion no abrio la columna")
        self.assertEqual(set(after), set(before), "la migracion no debe agregar ni quitar columnas")

        with engine.begin() as conn:
            rows = conn.execute(text(
                "SELECT id, username, question_prompt, validation_status FROM audit_logs ORDER BY id"
            )).fetchall()
        self.assertEqual(len(rows), 2, "la migracion perdio filas")
        self.assertEqual(rows[0][3], "APROBADO")
        self.assertEqual(rows[1][3], "RECHAZADO_RBAC")

        # Y ahora se puede insertar el valor honesto, que antes reventaba.
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO audit_logs (id, username, question_prompt, validation_status) "
                "VALUES (3, 'usuario', 'sin trazabilidad', NULL)"
            ))
            val = conn.execute(text(
                "SELECT validation_status FROM audit_logs WHERE id = 3"
            )).scalar()
        self.assertIsNone(val)

        # El ALTER no reconstruye la tabla, asi que los indices siguen ahi.
        idx = {i["name"] for i in inspect(engine).get_indexes("audit_logs")}
        self.assertIn("ix_audit_logs_id", idx)
        self.assertIn("ix_audit_logs_timestamp", idx)

    def test_migration_is_idempotent(self):
        engine = self._legacy_db()
        self._migrate(engine)
        self._migrate(engine)   # segunda pasada: no debe hacer nada ni romper nada
        self._migrate(engine)

        with engine.begin() as conn:
            rows = conn.execute(text(
                "SELECT id, validation_status FROM audit_logs ORDER BY id"
            )).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual([r[1] for r in rows], ["APROBADO", "RECHAZADO_RBAC"])
        cols = {c["name"]: c for c in inspect(engine).get_columns("audit_logs")}
        self.assertTrue(cols["validation_status"]["nullable"])

    def test_migration_on_an_already_migrated_db_is_a_noop(self):
        """Base creada por el modelo nuevo: la migracion no la toca."""
        import app.modules.telemetry_audit.models  # noqa: F401 (registra la tabla)
        import app.modules.auth.models  # noqa: F401
        from app.core.database import Base
        from app.modules.auth.models import Role, User
        from app.modules.telemetry_audit.models import AuditLog

        engine = self.engine
        Base.metadata.create_all(engine, tables=[
            Role.__table__, User.__table__, AuditLog.__table__,
        ])
        self._migrate(engine)
        cols = {c["name"]: c for c in inspect(engine).get_columns("audit_logs")}
        self.assertTrue(cols["validation_status"]["nullable"])
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO audit_logs (username, question_prompt, validation_status) "
                "VALUES ('u', 'q', NULL)"
            ))

if __name__ == "__main__":
    unittest.main()

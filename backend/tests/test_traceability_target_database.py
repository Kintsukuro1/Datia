"""El SQL de la trazabilidad tiene que decir contra que base se ejecuto.

Sin esto, "copiar consulta SQL" entrega un texto que no se puede reproducir: las
mismas tablas (`movimientos`, `fact_ventas`) existen en varias bases del mismo
servidor, y pegado en otra falla con `no existe la relacion ...`. El usuario
lee ese error como "no hay datos" en vez de "estabas en la base equivocada".
"""
import unittest

from sqlalchemy import text

from conftest import (
    create_temp_pg_database,
    drop_temp_pg_database,
    pg_source_conn_attrs,
    pg_source_engine,
)

from app.core.database import SessionLocal
from app.modules.admin_catalog.models import CorporateConnection
import app.modules.admin_catalog.models  # noqa: F401
import app.modules.auth.models  # noqa: F401
import app.modules.telemetry_audit.models  # noqa: F401
import app.modules.chat_engine.models  # noqa: F401
import app.modules.chat_engine.schemas as chat_schemas


class TestTraceabilityCarriesTargetDatabase(unittest.TestCase):

    def setUp(self):
        self.db = SessionLocal()

    def tearDown(self):
        self.db.query(CorporateConnection).filter(
            CorporateConnection.name == "Conn movimientos_demo"
        ).delete(synchronize_session=False)
        self.db.commit()
        self.db.close()

    # --------------------------------------------------------------
    def test_schema_accepts_target_database(self):
        t = chat_schemas.TraceabilityAudit(
            sql_executed="SELECT 1", execution_time_ms=1, rows_returned=1,
            validation_status="APROBADO", schema_tables_used=["movimientos"],
            explanation="x", target_database="movimientos_maxi3d_sql",
        )
        self.assertEqual(t.target_database, "movimientos_maxi3d_sql")

    def test_target_database_is_optional_and_defaults_to_none(self):
        """Sin el dato no se inventa: `None` es 'no lo se', no la base actual."""
        t = chat_schemas.TraceabilityAudit(
            sql_executed="SELECT 1", execution_time_ms=1, rows_returned=1,
            validation_status="APROBADO", schema_tables_used=[], explanation="x",
        )
        self.assertIsNone(t.target_database)

    def test_executor_meta_carries_the_database_name(self):
        """`SQLExecutor` resuelve el nombre desde el conector, no desde settings.

        Va contra una base PostgreSQL desechable: lo que se verifica es que el
        nombre de la base viaje en `meta`, y eso no depende de settings.
        """
        import asyncio

        from app.modules.chat_engine.sql_executor import SQLExecutor

        pg_db = create_temp_pg_database("trace")
        eng = pg_source_engine(pg_db)
        try:
            with eng.begin() as c:
                c.execute(text("CREATE TABLE movimientos (periodo TEXT)"))
                c.execute(text("INSERT INTO movimientos VALUES ('2026-07'), ('2026-08')"))
        finally:
            eng.dispose()

        try:
            conn = CorporateConnection(
                name="Conn movimientos_demo",
                **pg_source_conn_attrs(pg_db),
                is_active=True,
                is_uploaded=False,
            )
            self.db.add(conn)
            self.db.commit()
            self.db.refresh(conn)

            rows, sql, meta, healed, label = asyncio.run(
                SQLExecutor.execute_with_self_healing(
                    target_db_path=conn,
                    question="movimientos por periodo",
                    initial_sql="SELECT periodo FROM movimientos ORDER BY periodo",
                    allowed_tables={"movimientos"},
                    blocked_columns=set(),
                    table_columns_map={"movimientos": ["periodo"]},
                )
            )
            self.assertEqual(meta["target_database"], pg_db)
            self.assertEqual([r["periodo"] for r in rows], ["2026-07", "2026-08"])
        finally:
            drop_temp_pg_database(pg_db)


if __name__ == "__main__":
    unittest.main()

"""DEMO_MODE: una instalacion real no recibe el seed de demostracion.

`init_db` sembraba SIEMPRE las cuentas demo (`admin`/`admin123` y compania) y el
conector de plataforma a la base de negocio. Con eso, cada arranque de una
instalacion real deja cuentas activas con las claves publicadas en el README.
El gate es `settings.DEMO_MODE_EFFECTIVE`: valor explicito si esta, y si no,
encendido solo fuera de production.

Este test usa una base SQLite temporal propia y NO la metadata compartida de la
suite: esa ya trae el seed de corridas anteriores y no probaria nada sobre lo
que `init_db` siembra en una base fresca. El `engine` global no se redirige a
proposito: `init_db` lo usa para `ensure_schema_migrations`, igual que en el
resto de los tests.

El caso ON existe para que esto no pase por verde con el seed roto: si el gate
se comiera tambien la siembra con demo encendida, el control falla.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.database import Base
from app.db.init_db import init_db
from app.modules.admin_catalog.models import CorporateConnection
from app.modules.auth.models import Domain, Role, User


class TestDemoModeGating(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _fresh_session(self, name: str):
        engine = create_engine(f"sqlite:///{os.path.join(self._tmp.name, name)}")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(bind=engine)
        db = sessionmaker(bind=engine)()
        self.addCleanup(db.close)
        return db

    def test_demo_off_does_not_seed_accounts_nor_connector(self):
        db = self._fresh_session("demo_off.db")

        with patch.object(settings, "DEMO_MODE", False):
            init_db(db)

        self.assertEqual(
            db.query(User).count(), 0,
            "DEMO_MODE apagado no debe sembrar cuentas demo",
        )
        self.assertEqual(
            db.query(CorporateConnection).count(), 0,
            "DEMO_MODE apagado no debe sembrar el conector de plataforma",
        )
        # Plataforma sigue igual: el gate es del seed demo, no de roles/dominios.
        self.assertGreater(db.query(Role).count(), 0)
        self.assertGreater(db.query(Domain).count(), 0)

    def test_auto_without_demo_mode_off_in_production(self):
        db = self._fresh_session("demo_auto_prod.db")

        with patch.object(settings, "DEMO_MODE", None), patch.object(
            settings, "ENVIRONMENT", "production"
        ):
            init_db(db)

        self.assertEqual(
            db.query(User).count(), 0,
            "sin DEMO_MODE explicito, production no debe sembrar cuentas demo",
        )
        self.assertEqual(db.query(CorporateConnection).count(), 0)

    def test_demo_on_seeds_accounts_and_connector(self):
        db = self._fresh_session("demo_on.db")

        with patch.object(settings, "DEMO_MODE", True):
            init_db(db)

        self.assertGreater(
            db.query(User).count(), 0,
            "DEMO_MODE=true debe sembrar las cuentas demo",
        )
        self.assertEqual(
            db.query(CorporateConnection).filter(
                CorporateConnection.is_uploaded == False  # noqa: E712
            ).count(),
            1,
            "DEMO_MODE=true debe sembrar el conector de plataforma",
        )


if __name__ == "__main__":
    unittest.main()

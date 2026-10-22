"""El proyecto guarda UTC naive; no puede volver `datetime.utcnow()`.

Python 3.12 lo marco deprecado y una version futura lo puede eliminar. El
warning de SQLAlchemy apuntaba a `schema.py`, pero el callable deprecado estaba
en los defaults de nuestros modelos. Este test fija el contrato: helper UTC
naive, ningun `utcnow` en el codigo de la app y defaults sin warnings.
"""

import ast
import datetime
import uuid
import warnings
from pathlib import Path

from main import app  # noqa: F401  registra los mappers de SQLAlchemy

from app.core.database import SessionLocal
from app.core.time import utcnow_naive
from app.modules.auth.models import Role


def test_utcnow_naive_is_naive_and_close_to_utc():
    now = utcnow_naive()
    real_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    assert now.tzinfo is None
    assert abs((real_utc - now).total_seconds()) < 5


def test_no_utcnow_calls_in_app_source():
    app_root = Path(__file__).resolve().parents[1] / "app"
    offenders = []

    for path in app_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "utcnow":
                offenders.append(f"{path.relative_to(app_root)}:{node.lineno}")

    assert not offenders, "Volvio datetime.utcnow(): " + ", ".join(offenders)


def test_role_created_at_default_does_not_warn():
    db = SessionLocal()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            role = Role(name=f"test_utc_{uuid.uuid4().hex[:8]}")
            db.add(role)
            db.flush()

            assert role.created_at is not None
            assert role.created_at.tzinfo is None
    finally:
        db.rollback()
        db.close()

"""Arrancar sin la base de metadata creada tiene que crearla, no morir.

El motor principal conecta en tiempo de import de `app.core.database`, mucho
antes de que el lifespan de `main.py` llegue a `setup_postgres_full.run()`, que
es quien crea las bases. En una instalación nueva el proceso moría con
`FATAL: no existe la base de datos "democratizacion_metadatos"`.

Se verifica en un subproceso a propósito: el comportamiento es el import, y en
este proceso el módulo ya está importado y apunta a la base de pruebas.
"""

import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

from conftest import drop_temp_pg_database

BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_import_creates_missing_metadata_database():
    name = f"tmp_autocreate_{int(time.time())}_{secrets.token_hex(3)}"[:60]
    env = {**os.environ, "POSTGRES_DB": name}
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import app.core.database"],
            cwd=BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        # Exit 0 solo es posible si el `engine` conectó: la base existe.
        assert result.returncode == 0, result.stderr
    finally:
        drop_temp_pg_database(name)

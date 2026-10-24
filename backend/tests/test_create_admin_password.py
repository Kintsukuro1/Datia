"""La clave del primer admin la elige un operador: piso mas alto que el global."""
import pathlib
import subprocess
import sys
import unittest

from scripts.create_admin import MIN_ADMIN_PASSWORD_LENGTH, _validate_admin_password

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]


class TestAdminPassword(unittest.TestCase):
    def test_short_password_is_rejected(self):
        with self.assertRaises(ValueError):
            _validate_admin_password("corta123")

    def test_minimum_length_is_accepted(self):
        password = "a" * MIN_ADMIN_PASSWORD_LENGTH
        self.assertEqual(_validate_admin_password(password), password)

    def test_the_floor_is_higher_than_the_global_one(self):
        self.assertGreaterEqual(MIN_ADMIN_PASSWORD_LENGTH, 12)


class TestAdminScriptModelRegistration(unittest.TestCase):
    def test_script_registers_every_model(self):
        """Sin los imports de modelos, `create_admin` revienta en el primer query.

        El script corre en un proceso propio (`python -m scripts.create_admin`),
        asi que no hereda los imports de `main.py`. Este test reproduce ese
        arranque en limpio y configura los mappers: si falta un modulo de
        modelos, SQLAlchemy falla con "failed to locate a name".
        """
        code = (
            "import scripts.create_admin\n"
            "from sqlalchemy.orm import configure_mappers\n"
            "configure_mappers()\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

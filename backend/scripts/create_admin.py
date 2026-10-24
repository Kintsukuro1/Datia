"""Crea el primer administrador de una instalacion on-prem.

La instalacion de produccion arranca con `DEMO_MODE=false`: no hay cuentas demo
ni claves publicadas. El primer admin se crea con este CLI, sobre la metadata ya
inicializada por el backend:

    docker compose exec -T backend python -m scripts.create_admin \
        --username admin --email admin@empresa.com

La clave se lee de `DATIA_ADMIN_PASSWORD` o se pide por consola. La cuenta nace
con `must_change_password=True`: la clave inicial sirve para entrar una vez y el
backend obliga a cambiarla antes de operar.
"""
import argparse
import getpass
import os
import sys

from app.core.constants import ROLE_ADMINISTRADOR
from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.core.time import utcnow_naive
# SQLAlchemy resuelve los relationship() por nombre recien al configurar los
# mappers. Importar solo `auth.models` deja `RoleDomainLink` sin registrar y la
# primera consulta falla con "expression 'RoleDomainLink' failed to locate a
# name"; el script corre en un proceso aparte, sin los imports de `main.py`.
# Es el mismo set que carga `app.db.init_db`.
import app.modules.admin_catalog.models  # noqa: F401
import app.modules.chat_engine.models  # noqa: F401
import app.modules.telemetry_audit.models  # noqa: F401
from app.modules.auth.models import Role, User
from app.modules.auth.schemas import validate_password_strength

# La clave del primer admin la elige un operador, no el servidor. El minimo
# global (6) es para claves temporales; esta cuenta existe para configurar toda
# la instalacion y su cambio se exige recien en el primer login.
MIN_ADMIN_PASSWORD_LENGTH = 12


def _validate_admin_password(password: str) -> str:
    password = validate_password_strength(password)
    if len(password) < MIN_ADMIN_PASSWORD_LENGTH:
        raise ValueError(
            "la clave del administrador debe tener al menos "
            f"{MIN_ADMIN_PASSWORD_LENGTH} caracteres."
        )
    return password


def _read_password() -> str:
    password = os.environ.get("DATIA_ADMIN_PASSWORD")
    if password:
        return password
    first = getpass.getpass("Contrasena inicial del admin: ")
    second = getpass.getpass("Repetir contrasena: ")
    if first != second:
        raise SystemExit("Las contrasenas no coinciden.")
    return first


def create_admin(username: str, password: str, email: str | None) -> int:
    username = username.strip()
    if not username:
        print("ERROR: el usuario no puede estar vacio.", file=sys.stderr)
        return 1

    db = SessionLocal()
    try:
        if db.query(User).filter(User.username == username).first():
            print(
                f"ERROR: el usuario '{username}' ya existe. "
                "Use el panel de administracion para resetear su clave.",
                file=sys.stderr,
            )
            return 2

        if email and db.query(User).filter(User.email == email).first():
            print(f"ERROR: el email '{email}' ya esta registrado.", file=sys.stderr)
            return 2

        try:
            password = _validate_admin_password(password)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

        role = db.query(Role).filter(Role.name == ROLE_ADMINISTRADOR).first()
        if role is None:
            print(
                "ERROR: no existe el rol de plataforma. Arranque el backend una vez "
                "para que inicialice la metadata y vuelva a intentar.",
                file=sys.stderr,
            )
            return 1

        db.add(User(
            username=username,
            email=email or None,
            hashed_password=get_password_hash(password),
            is_admin=True,
            is_active=True,
            role_id=role.id,
            failed_login_attempts=0,
            locked_until=None,
            must_change_password=True,
            temp_password_issued_at=utcnow_naive(),
        ))
        db.commit()
        print(f"Admin '{username}' creado. Debe cambiar la contrasena al entrar.")
        return 0
    except Exception as exc:
        db.rollback()
        print(f"ERROR: no se pudo crear el admin: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Crea el primer administrador de Dat.ia (sin cuentas demo)."
    )
    parser.add_argument(
        "--username",
        default=os.environ.get("DATIA_ADMIN_USERNAME", "admin"),
        help="Usuario del admin (default: DATIA_ADMIN_USERNAME o 'admin').",
    )
    parser.add_argument(
        "--email",
        default=os.environ.get("DATIA_ADMIN_EMAIL"),
        help="Email del admin (opcional).",
    )
    args = parser.parse_args()
    return create_admin(args.username, _read_password(), args.email)


if __name__ == "__main__":
    raise SystemExit(main())

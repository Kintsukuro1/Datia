import datetime
import logging
from typing import Generator, Optional
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
import app.modules.auth.models  # noqa: F401 - registra el modelo en SQLAlchemy
import app.modules.admin_catalog.models  # noqa: F401
import app.modules.telemetry_audit.models  # noqa: F401
import app.modules.chat_engine.models  # noqa: F401
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import decode_token_payload, hash_api_key
from app.modules.auth.models import ApiClient
from app.core.time import utcnow_naive
from app.modules.auth.models import User, UserSession
from app.modules.telemetry_audit.models import AuditLog
from app.core.constants import (
    SESSION_LAST_SEEN_UPDATE_INTERVAL_MINUTES,
    ADMIN_ROLES,
    ROLE_ADMINISTRADOR,
)

logger = logging.getLogger(__name__)

# Topes de la evidencia que se guarda en `audit_logs`. Un prompt o un snapshot
# sin tope puede hacer fallar el INSERT (o inflar la tabla) y, como la auditoria
# era best-effort, la consulta igual devolvia filas sin registro. El prompt se
# trunca con una marca visible; el snapshot se omite y el export responde 409
# ("sin resultado exportable") en vez de guardar JSON corrupto.
AUDIT_PROMPT_MAX_CHARS = 32_000
AUDIT_SNAPSHOT_MAX_CHARS = 1_000_000


class AuditWriteError(RuntimeError):
    """La auditoria obligatoria no se pudo persistir."""


oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

def get_db() -> Generator:
    """Provides a database session for requests."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def _password_change_pending_allowed(request: Request) -> bool:
    """
    Rutas que siguen accesibles con `must_change_password=True` o clave vencida.

    Sin las dos primeras el usuario queda atrapado: no puede ni ver su propio flag
    (`/auth/me`) ni limpiarlo (`/auth/change-password`), porque ambas pasan por acá.
    `/auth/logout` tambien queda abierta: una sesión que no sirve para nada más
    tiene que poder cerrarse.
    """
    path = request.url.path.rstrip("/")
    return (
        path.endswith("/auth/change-password")
        or path.endswith("/auth/me")
        or path.endswith("/auth/logout")
    )


def temporary_password_expired(user: User) -> bool:
    """`True` si la cuenta tiene una clave temporal emitida y ya venció.

    La fecha se guarda UTC naive (mismo criterio que el resto de la base). Una
    cuenta sin clave temporal emitida —incluidas las existentes, que quedan NULL
    tras la migración— no se bloquea por un dato ausente.
    """
    expires_at = getattr(user, "temp_password_expires_at", None)
    if not expires_at:
        return False
    now = utcnow_naive()
    if getattr(expires_at, "tzinfo", None) is not None:
        expires_at = expires_at.replace(tzinfo=None)
    return now >= expires_at


def _load_active_session(db: Session, user: User, payload: dict):
    """Devuelve `(session, motivo)` para el JTI del token.

    `motivo` es `None` si la sesion sirve, o uno de `missing`, `revoked`,
    `idle`, `absolute`. La fila de sesion es obligatoria: antes, si el JTI no
    tenia fila, el token seguia valiendo hasta expirar aunque la sesion se
    hubiera borrado. Los topes de inactividad y absoluto se evaluan aca, no en
    el JWT, porque el JWT no se puede revocar sin tocar la base.
    """
    jti = payload.get("jti")
    if not jti:
        return None, "missing"

    session = db.query(UserSession).filter(
        UserSession.jti == jti,
        UserSession.user_id == user.id,
    ).first()
    if session is None:
        return None, "missing"
    if session.is_revoked:
        return session, "revoked"

    now = utcnow_naive()

    def _naive(value):
        if isinstance(value, str):
            try:
                value = datetime.datetime.fromisoformat(value)
            except Exception:
                return None
        if value is not None and getattr(value, "tzinfo", None) is not None:
            value = value.replace(tzinfo=None)
        return value

    created_at = _naive(session.created_at)
    last_seen = _naive(session.last_seen_at)

    if created_at and (
        now - created_at
    ).total_seconds() > settings.SESSION_ABSOLUTE_TIMEOUT_MINUTES * 60:
        session.is_revoked = True
        try:
            db.commit()
        except Exception:
            db.rollback()
        return session, "absolute"

    if last_seen and (
        now - last_seen
    ).total_seconds() > settings.SESSION_IDLE_TIMEOUT_MINUTES * 60:
        session.is_revoked = True
        try:
            db.commit()
        except Exception:
            db.rollback()
        return session, "idle"

    # Si el tope de inactividad es menor que el intervalo de refresco, hay que
    # escribir `last_seen_at` en cada request; si no, una sesión activa se vería
    # "inactiva" solo porque el refresh todavía no corrió.
    refresh_interval = SESSION_LAST_SEEN_UPDATE_INTERVAL_MINUTES
    must_refresh = (
        last_seen is None
        or settings.SESSION_IDLE_TIMEOUT_MINUTES <= refresh_interval
        or (now - last_seen).total_seconds() > refresh_interval * 60
    )
    if must_refresh:
        session.last_seen_at = now
        try:
            db.commit()
        except Exception:
            db.rollback()

    return session, None


def _user_from_api_key(db: Session, api_key: str) -> Optional[User]:
    """Resuelve una credencial de servicio activa a su cuenta de servicio.

    La clave en claro nunca se guarda ni se loguea. La cuenta de servicio es un
    `User` normal (no admin) para que RBAC/CLS y la auditoría sigan funcionando
    sin abrir un camino paralelo.
    """
    # Los tests llaman la dependency directo y reciben el objeto `Header` por
    # default; solo un string es una credencial real.
    if not isinstance(api_key, str) or not api_key.startswith("datia_sk_"):
        return None
    client = db.query(ApiClient).filter(
        ApiClient.key_hash == hash_api_key(api_key),
        ApiClient.is_active == True,
    ).first()
    if client is None:
        return None
    user = db.query(User).filter(
        User.id == client.user_id,
        User.is_active == True,
    ).first()
    if user is None:
        return None

    now = utcnow_naive()
    if client.last_used_at is None or (
        now - client.last_used_at
    ).total_seconds() > 300:
        client.last_used_at = now
        try:
            db.commit()
        except Exception:
            db.rollback()
    return user


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    token: Optional[str] = Depends(oauth2_scheme_optional),
    api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> User:
    """
    Acepta Bearer (usuario) o X-API-Key (servicio) y valida la sesión.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No se pudieron validar las credenciales de sesión.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if isinstance(api_key, str) and api_key:
        service_user = _user_from_api_key(db, api_key)
        if service_user is None:
            raise credentials_exception
        return service_user

    if not token:
        raise credentials_exception
    payload = decode_token_payload(token)
    if not payload:
        raise credentials_exception
        
    user_id = payload.get("sub")
    if user_id is None:
        raise credentials_exception

    try:
        user = db.query(User).filter(User.id == int(user_id)).first()
    except Exception:
        raise credentials_exception

    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Usuario inactivo.")

    # La sesion es obligatoria: un JWT sin fila de `user_sessions` ya no vale.
    _, reason = _load_active_session(db, user, payload)
    if reason:
        messages = {
            "missing": "La sesión no existe o expiró. Inicia sesión nuevamente.",
            "revoked": "La sesión ha sido revocada o cerrada. Inicia sesión nuevamente.",
            "idle": "La sesión expiró por inactividad. Inicia sesión nuevamente.",
            "absolute": "La sesión expiró. Inicia sesión nuevamente.",
        }
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=messages.get(reason, messages["missing"]),
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Una credencial temporal que el usuario puede ignorar para siempre no es una
    # credencial: `admin_reset_user_password` ponía el flag en True y
    # `change_user_password` lo limpiaba, pero nada impedía usar el token con la
    # contraseña temporal indefinidamente. Va acá, y no en cada router, porque esta
    # es la ÚNICA dependencia por la que pasan todos los endpoints autenticados:
    # `get_current_admin` cuelga de acá y cubre los endpoints de administración.
    #
    # Dos casos distintos, mismo corte:
    #   - `must_change_password`: el admin reseteó la clave, cambio inmediato.
    #   - clave temporal vencida: la cuenta nació con una clave del admin y el
    #     usuario no la cambió en TEMP_PASSWORD_TTL_DAYS.
    if not _password_change_pending_allowed(request):
        if temporary_password_expired(user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Tu contraseña temporal venció. Debes cambiarla antes de continuar. "
                    "Usa POST /api/v1/auth/change-password."
                )
            )
        if user.must_change_password:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Debes cambiar tu contraseña temporal antes de continuar. "
                    "Usa POST /api/v1/auth/change-password."
                )
            )

    return user

def get_current_user_optional(
    db: Session = Depends(get_db),
    token: Optional[str] = Depends(oauth2_scheme_optional),
    api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> Optional[User]:
    """
    Safely retrieves authenticated user if a valid JWT token is provided.
    Returns None if token is absent, invalid, expired or revoked, without raising HTTP 401.
    """
    if isinstance(api_key, str) and api_key:
        return _user_from_api_key(db, api_key)
    if not token:
        return None

    payload = decode_token_payload(token)
    if not payload:
        return None

    user_id = payload.get("sub")
    if user_id is None:
        return None

    try:
        user = db.query(User).filter(User.id == int(user_id)).first()
    except Exception:
        return None

    if user is None or not user.is_active:
        return None

    _, reason = _load_active_session(db, user, payload)
    if reason:
        return None

    # Este dependency es el de los endpoints publicos que, si hay token, agregan
    # contexto del rol (hoy solo /chat/suggestions). Una clave temporal vencida
    # no puede seguir viendo sugerencias armadas con sus tablas autorizadas: se
    # degrada a anonimo, igual que un token invalido. No se lanza 403 porque el
    # endpoint sigue siendo publico por diseno.
    if temporary_password_expired(user) or user.must_change_password:
        return None

    return user

def is_admin_user(user: User) -> bool:
    """"¿Este usuario administra?" con la MISMA regla que `get_current_admin`.

    No alcanza con el flag `is_admin`: un usuario con rol del catalogo que este
    en ADMIN_ROLES entra igual a las pantallas de administracion, asi que
    contarlo como no-admin dejaria pasar el guard de "ultimo administrador" y
    el sistema se quedaria sin nadie que pueda administrarlo.
    """
    return bool(user.is_admin) or (user.role is not None and user.role.name in ADMIN_ROLES)

def get_current_admin(
    current_user: User = Depends(get_current_user)
) -> User:
    """Ensures current user has Administrator privileges."""
    if not is_admin_user(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado. Se requieren privilegios de Administrador."
        )
    return current_user

def _persist_audit_log(
    db: Session,
    user_id: Optional[int],
    username: str,
    user_role: Optional[str],
    question_prompt: str,
    sql_generated: Optional[str],
    # `None` = "esta consulta no tiene registro de validacion". NO es un valor
    # decorativo: el export de compliance lee esta columna de la BD y antes
    # confundia "no lo se" con "APROBADO", que es una afirmacion distinta.
    validation_status: Optional[str],
    target_database: str,
    execution_time_ms: int = 0,
    rows_returned: int = 0,
    error_message: Optional[str] = None,
    result_snapshot: Optional[str] = None,
    required: bool = False,
) -> Optional[int]:
    """Persiste un `AuditLog` y devuelve su ID.

    `required=True` es para los caminos que DEVUELVEN DATOS DEL CLIENTE: si la
    evidencia no se puede guardar, la respuesta no sale. Los eventos de auth y
    administracion siguen siendo best-effort para no convertir un fallo de
    auditoria en un lockout; el llamador que necesite lo contrario pasa
    `required=True`.
    """
    if question_prompt and len(question_prompt) > AUDIT_PROMPT_MAX_CHARS:
        question_prompt = (
            question_prompt[:AUDIT_PROMPT_MAX_CHARS] + "\n...[truncado por tamaño]"
        )
    if result_snapshot and len(result_snapshot) > AUDIT_SNAPSHOT_MAX_CHARS:
        logger.warning(
            "Snapshot de auditoria omitido: %s caracteres (tope %s).",
            len(result_snapshot),
            AUDIT_SNAPSHOT_MAX_CHARS,
        )
        result_snapshot = None

    try:
        audit_entry = AuditLog(
            user_id=user_id,
            username=username,
            user_role=user_role,
            question_prompt=question_prompt,
            sql_generated=sql_generated,
            validation_status=validation_status,
            target_database=target_database,
            execution_time_ms=execution_time_ms,
            rows_returned=rows_returned,
            error_message=error_message,
            result_snapshot=result_snapshot
        )
        db.add(audit_entry)
        db.commit()
        db.refresh(audit_entry)
        return audit_entry.id
    except Exception as e:
        db.rollback()
        if required:
            raise AuditWriteError(
                "No se pudo registrar la auditoría de la consulta. "
                "La respuesta se cancela para no devolver datos sin trazabilidad."
            ) from e
        logger.warning(f"Error registrando auditoria: {e}")
        return None

def display_role_name(user: User) -> Optional[str]:
    """Nombre del rol para MOSTRAR, o `None` si la cuenta no tiene ninguno.

    Antes cada router se resolvia el nombre por su cuenta con un fallback:

        role_name = user.role.name if user.role else (
            "Super Administrador" if user.is_admin else "Usuario"
        )

    Ninguno de esos tres nombres existe en el catalogo de 8 roles, asi que la
    etiqueta no describia nada: era texto libre que recien despues se comparaba
    contra nombres reales. Para lo visual no era grave; para auditoria si.

    `ROLE_ADMINISTRADOR` para el admin sin fila de rol NO es inventar un nombre:
    `is_admin` es un hecho persistido en la columna y esa persona administra en
    todas las capas de auth. Le corresponde el nombre real del catalogo. Es la
    misma decision que toma `require_assigned_role` de abajo.

    `None` para la cuenta sin rol y sin `is_admin`: el rol no se sabe, y el
    contrato lo admite (`UserOut.role_name` y `AuditLog.user_role` son ambos
    `Optional`). Un nombre inventado seria un hecho falso en la evidencia.

    Vive aca y no en `auth/router.py` porque lo necesitan al menos dos modulos
    (`auth` y `reports`) y ninguno de los dos es el piso comun: `deps` ya es el
    piso que comparten, ya tiene la otra mitad de esta misma regla
    (`require_assigned_role`) y no importa a `reports`, asi que no crea ciclo.
    """
    if user.role:
        return user.role.name
    if user.is_admin:
        return ROLE_ADMINISTRADOR
    return None


def require_assigned_role(
    user: User, db: Session, question: str, target_database: str
) -> str:
    """Nombre del rol de la cuenta, o 403 si no tiene ninguno asignado.

    El corte mira el REGISTRO (`user.role`), no el nombre resuelto. Antes cada
    endpoint resolvia el nombre con un fallback:

        role_name = user.role.name if user.role else ROLE_USUARIO

    Una cuenta con `role_id = NULL` y `is_admin = False` caia en "Usuario
    Consultor", que es truthy y es un rol REAL y VALIDO del catalogo. El guard
    resuelve por nombre cuando `role_id` es None (a proposito: asi funcionan sus
    tests y el self-healing), asi que la cuenta sin rol heredaba la matriz del
    Consultor. La cuenta sin rol y el Consultor son dos hechos distintos, y el que
    decide es si existe la fila.

    Vive aca y no en `chat_engine/router.py` porque lo necesitan endpoints de
    `admin_catalog` y `system`, que no importan a `chat_engine` en module scope
    (lo hacen con imports diferidos, justo para no crear el acoplamiento). Este
    modulo es el que YA comparte `get_current_admin` con esos routers, asi que no
    crea ninguna arista nueva: `deps` es el piso comun de dependencias.

    El admin sin fila de rol NO entra por aca: `is_admin` manda y recibe el
    nombre de administrador, que es lo que el resto del pipeline espera.

    El 403 va con su registro en auditoria: una denegacion que no deja rastro
    no es una denegacion. Mismo `RECHAZADO_RBAC` que el motor escribe cuando la
    peticion llega hasta el.
    """
    if user.role:
        return user.role.name
    if user.is_admin:
        return ROLE_ADMINISTRADOR

    _persist_audit_log(
        db=db,
        user_id=user.id,
        username=user.username,
        # `None` y no un nombre inventado: la columna es nullable justamente
        # para este caso. Poner "Usuario Consultor" seria afirmar un rol que la
        # cuenta no tiene, en el registro que el compliance lee.
        user_role=None,
        question_prompt=question,
        sql_generated=None,
        validation_status="RECHAZADO_RBAC",
        target_database=target_database,
        error_message="Cuenta sin rol asignado",
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            "Tu cuenta todavia no tiene un rol asignado. Un Administrador debe "
            "asignarte un perfil corporativo para acceder a los datos."
        ),
    )

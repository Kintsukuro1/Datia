import datetime
import math
import secrets
import uuid
from typing import List, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.api.deps import (
    get_db, get_current_user, get_current_admin, display_role_name, is_admin_user,
    _persist_audit_log,
)
from app.core.security import verify_password, get_password_hash, create_access_token, decode_token_payload
from app.core.config import settings
from app.core.time import utcnow_naive
from app.modules.auth.models import User, Role, Domain, UserSession
from app.modules.admin_catalog.models import SemanticCatalog
from app.core.constants import (
    MAX_FAILED_LOGIN_ATTEMPTS, ACCOUNT_LOCKOUT_DURATION_MINUTES, ADMIN_ROLES,
    DEFAULT_USER_ROLE,
)
from app.modules.auth.schemas import (
    UserLogin, UserAdminCreate, UserCreationResponse, UserOut, Token,
    PasswordChangeRequest, PasswordResetResponse, SessionOut, UserRoleUpdate,
    RoleCreate, RoleUpdate, RoleOut, DomainCreate, DomainUpdate, DomainOut,
    validate_password_strength
)

def _require_strong_password(raw: str) -> str:
    """Aplica la regla única de schemas.validate_password_strength como HTTP 400."""
    try:
        return validate_password_strength(raw)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

def _generate_temp_password() -> str:
    """Clave temporal de alta entropía, generada solo en el servidor.

    `token_urlsafe(18)` son ~24 caracteres aleatorios (144 bits). No se guarda en
    claro en ningún lado: la respuesta HTTP es la única vez que se muestra.
    """
    return f"Datia-{secrets.token_urlsafe(18)}"

def _jti_from_request(request: Request) -> Optional[str]:
    """
    JTI de la sesión con la que se está haciendo el request.

    `get_current_user` solo devuelve el User, y revocar "todas menos la actual"
    necesita saber cuál es la actual para no expulsar al propio usuario de su
    sesión recién autenticada.
    """
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    payload = decode_token_payload(header.split(" ", 1)[1].strip())
    return payload.get("jti") if payload else None

def _audit_login_failure(db: Session, user: Optional[User], username: str, reason: str) -> None:
    """Registra un login fallido SIN guardar la clave intentada.

    `AuditLog.username` es de 50 caracteres y el body de login no valida largo:
    el username se trunca acá para que un intento con un nombre gigante no haga
    fallar la auditoría (que es best-effort y se tragaría el error).
    """
    safe_username = (username or "")[:50]
    _persist_audit_log(
        db=db,
        user_id=user.id if user else None,
        username=safe_username,
        user_role=display_role_name(user) if user else None,
        question_prompt=f"Intento de inicio de sesión fallido para '{safe_username}': {reason}.",
        sql_generated=None,
        validation_status="AUTH_LOGIN_FALLIDO",
        target_database="metadata",
        error_message=reason,
    )

router = APIRouter()

@router.post("/users", response_model=UserCreationResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    user_in: UserAdminCreate,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
) -> Any:
    """Crea una cuenta (Admin only) con una clave temporal generada por el servidor.

    No existe alta pública: el único camino para que nazca una cuenta es este.
    La cuenta nace con `must_change_password=True`: la temporal sirve para entrar,
    pero el resto de la API responde 403 hasta que la cambie. Además vence a los
    `TEMP_PASSWORD_TTL_DAYS` días, así que una temporal que nunca se cambió no
    queda viva para siempre.
    """
    username = user_in.username.strip()
    if not username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El nombre de usuario no puede estar vacío."
        )

    existing_username = db.query(User).filter(User.username == username).first()
    if existing_username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El nombre de usuario ya está registrado."
        )

    if user_in.email:
        existing_email = db.query(User).filter(User.email == user_in.email).first()
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El correo electrónico ya está registrado."
            )

    default_role = db.query(Role).filter(Role.name == DEFAULT_USER_ROLE).first()
    temporary_password = _generate_temp_password()
    now = utcnow_naive()

    new_user = User(
        username=username,
        email=user_in.email,
        hashed_password=get_password_hash(temporary_password),
        is_admin=False,
        is_active=True,
        role_id=default_role.id if default_role else None,
        failed_login_attempts=0,
        locked_until=None,
        must_change_password=True,
        temp_password_issued_at=now,
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    user_out = UserOut.model_validate(new_user)
    user_out.role_name = display_role_name(new_user)

    _persist_audit_log(
        db=db,
        user_id=current_admin.id,
        username=current_admin.username,
        user_role=display_role_name(current_admin),
        question_prompt=f"Alta de cuenta '{new_user.username}' por administrador.",
        sql_generated=None,
        validation_status="AUTH_ALTA_CUENTA",
        target_database="metadata",
    )

    return UserCreationResponse(
        message=(
            f"Cuenta '{new_user.username}' creada. Entrega la contraseña temporal "
            f"al usuario: solo se muestra esta vez. Debe cambiarla en su primer "
            f"inicio de sesión; la temporal vence en "
            f"{settings.TEMP_PASSWORD_TTL_DAYS} días."
        ),
        temporary_password=temporary_password,
        user=user_out,
    )

@router.post("/login", response_model=Token)
def login_user(
    login_data: UserLogin,
    request: Request,
    db: Session = Depends(get_db)
) -> Any:
    """
    Authenticates user, checks account lockout policy,
    tracks failed attempts, creates an active UserSession and audits the result.
    """
    user = db.query(User).filter(User.username == login_data.username).first()
    if not user:
        _audit_login_failure(db, None, login_data.username, "Usuario inexistente")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario o contraseña incorrectos."
        )

    now = utcnow_naive()

    # 1. Check lockout expiration or active lockout
    if user.locked_until:
        if user.locked_until > now:
            remaining_minutes = max(1, math.ceil((user.locked_until - now).total_seconds() / 60))
            _audit_login_failure(
                db, user, login_data.username,
                f"Cuenta bloqueada temporalmente; intento rechazado ({remaining_minutes} min restantes)",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Cuenta bloqueada temporalmente por {MAX_FAILED_LOGIN_ATTEMPTS} intentos fallidos. Intenta nuevamente en {remaining_minutes} minuto(s) o solicita a un Administrador que restablezca tu acceso."
            )
        else:
            # Lockout expired, reset counters
            user.locked_until = None
            user.failed_login_attempts = 0
            db.commit()

    # 2. Check credentials
    if not verify_password(login_data.password, user.hashed_password):
        user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
        if user.failed_login_attempts >= MAX_FAILED_LOGIN_ATTEMPTS:
            user.locked_until = now + datetime.timedelta(minutes=ACCOUNT_LOCKOUT_DURATION_MINUTES)
            user.failed_login_attempts = 0
            db.commit()
            _audit_login_failure(
                db, user, login_data.username,
                f"Contraseña incorrecta; cuenta bloqueada por {ACCOUNT_LOCKOUT_DURATION_MINUTES} minutos",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Has superado el límite de {MAX_FAILED_LOGIN_ATTEMPTS} intentos fallidos. Tu cuenta ha sido bloqueada por {ACCOUNT_LOCKOUT_DURATION_MINUTES} minutos."
            )
        db.commit()
        remaining_attempts = MAX_FAILED_LOGIN_ATTEMPTS - user.failed_login_attempts
        _audit_login_failure(db, user, login_data.username, "Contraseña incorrecta")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Usuario o contraseña incorrectos. Te quedan {remaining_attempts} intento(s) antes del bloqueo."
        )

    # 3. Account active check
    if not user.is_active:
        _audit_login_failure(db, user, login_data.username, "Cuenta desactivada")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La cuenta de usuario está desactivada."
        )

    # Reset failure counter upon successful authentication
    user.failed_login_attempts = 0
    user.locked_until = None

    # 4. Generate JWT with unique JTI and persist active session
    session_jti = str(uuid.uuid4())
    access_token = create_access_token(subject=user.id, jti=session_jti)

    # Extract client IP and user-agent
    client_ip = request.client.host if request.client else "127.0.0.1"
    user_agent = request.headers.get("user-agent", "Desktop Client")

    user_session = UserSession(
        user_id=user.id,
        jti=session_jti,
        created_at=now,
        last_seen_at=now,
        ip_address=client_ip[:50] if client_ip else "127.0.0.1",
        user_agent=user_agent[:255] if user_agent else "Desktop Client",
        is_revoked=False
    )
    db.add(user_session)
    db.commit()

    _persist_audit_log(
        db=db,
        user_id=user.id,
        username=user.username,
        user_role=display_role_name(user),
        question_prompt=f"Inicio de sesión exitoso desde {client_ip}.",
        sql_generated=None,
        validation_status="AUTH_LOGIN_OK",
        target_database="metadata",
    )

    user_out = UserOut.model_validate(user)
    user_out.role_name = display_role_name(user)

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user_out
    }

@router.get("/me", response_model=UserOut)
def read_current_user_profile(
    current_user: User = Depends(get_current_user)
) -> Any:
    """Returns profile of currently authenticated user."""
    user_out = UserOut.model_validate(current_user)
    user_out.role_name = display_role_name(current_user)
    return user_out

@router.post("/logout")
def logout(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> Any:
    """Cierra la sesión actual revocando su JTI.

    Antes el logout del frontend solo borraba el token del navegador: el JWT
    seguía siendo válido hasta 24 h y un token copiado sobrevivía al cierre.
    Ahora la sesión queda revocada en `user_sessions` y `get_current_user` la
    rechaza en el siguiente request.
    """
    current_jti = _jti_from_request(request)
    revoked = 0
    if current_jti:
        revoked = db.query(UserSession).filter(
            UserSession.user_id == current_user.id,
            UserSession.jti == current_jti,
            UserSession.is_revoked == False,
        ).update({"is_revoked": True})
        db.commit()

    _persist_audit_log(
        db=db,
        user_id=current_user.id,
        username=current_user.username,
        user_role=display_role_name(current_user),
        question_prompt=f"Cierre de sesión: {revoked} sesión(es) revocada(s).",
        sql_generated=None,
        validation_status="AUTH_LOGOUT",
        target_database="metadata",
    )

    return {"message": "Sesión cerrada exitosamente.", "revoked_sessions": revoked}

@router.post("/change-password")
def change_user_password(
    pwd_in: PasswordChangeRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> Any:
    """Allows authenticated user to change password and clears must_change_password flag."""
    if not verify_password(pwd_in.old_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La contraseña actual ingresada es incorrecta."
        )

    new_password = _require_strong_password(pwd_in.new_password)

    # Reutilizar la temporal dejaría la credencial que el admin conoce viva
    # aunque el flag se haya limpiado. Se compara contra el hash actual para no
    # necesitar guardar la clave en claro.
    if verify_password(new_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La nueva contraseña debe ser diferente a la actual."
        )

    current_user.hashed_password = get_password_hash(new_password)
    current_user.must_change_password = False
    current_user.temp_password_issued_at = None
    current_user.failed_login_attempts = 0
    current_user.locked_until = None

    # Revoca el resto de las sesiones del usuario, igual que hace el reset de
    # admin. Sin esto, un token robado seguia valiendo hasta expirar: el usuario
    # detectaba el robo, cambiaba la contraseña y la credencial expuesta
    # sobrevivia al cambio. Se conserva la sesión con la que se está haciendo el
    # cambio para que el cliente no quede desconectado.
    current_jti = _jti_from_request(request)
    revoke_filter = [
        UserSession.user_id == current_user.id,
        UserSession.is_revoked == False,
    ]
    if current_jti:
        revoke_filter.append(UserSession.jti != current_jti)
    revoked = db.query(UserSession).filter(*revoke_filter).update({"is_revoked": True})

    db.commit()

    _persist_audit_log(
        db=db,
        user_id=current_user.id,
        username=current_user.username,
        user_role=display_role_name(current_user),
        question_prompt="Cambio de contraseña de la cuenta.",
        sql_generated=None,
        validation_status="AUTH_CAMBIO_CLAVE",
        target_database="metadata",
    )

    return {"message": "Contraseña actualizada exitosamente.", "revoked_sessions": revoked}

# =========================================================================
# ADMIN GOVERNANCE ENDPOINTS: SESSIONS & PASSWORD RESETS
# =========================================================================

# =========================================================================
# ADMIN GOVERNANCE ENDPOINTS: CÁTALOGO DE ROLES & DOMINIOS
# =========================================================================
#
# El catálogo de roles dejó de ser solo de lectura: sin alta/edición/baja, cada
# cliente nuevo dependía de los 8 perfiles de la demo y no podía modelar su
# organigrama. Los dominios son las áreas de negocio a las que se adscribe el
# catálogo semántico (`semantic_catalog.domain_id`).
#
# Invariantes:
#   - el rol de `ADMIN_ROLES` no se renombra ni se borra: `is_admin_user` lo
#     reconoce por NOMBRE exacto, así que renombrarlo dejaría a sus usuarios sin
#     panel de administración en el siguiente request, sin aviso;
#   - un rol con cuentas asignadas no se borra (409): `User.role_id` es
#     `ON DELETE SET NULL` en la base, así que borrarlo dejaría cuentas sin
#     matriz y el chat les respondería "tu rol no tiene tablas", que parece un
#     bug del producto;
#   - toda mutación deja fila en `audit_logs`, sin secretos.

def _clean_catalog_name(raw: Optional[str], label: str) -> str:
    """Nombre visible: se recorta y no puede quedar vacío."""
    name = (raw or "").strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El nombre del {label} no puede estar vacío.",
        )
    return name


def _catalog_name_taken(
    db: Session, model: Any, name: str, exclude_id: Optional[int] = None
) -> bool:
    """Duplicado sin distinguir mayúsculas.

    Postgres compara `VARCHAR` distinguiendo mayúsculas, así que "Ventas" y
    "ventas" convivirían como dos roles distintos. Para una etiqueta que el
    admin elige a mano eso es una trampa, no una feature.
    """
    query = db.query(model.id).filter(func.lower(model.name) == name.lower())
    if exclude_id is not None:
        query = query.filter(model.id != exclude_id)
    return query.first() is not None


def _role_out(role: Role, user_count: int = 0) -> RoleOut:
    return RoleOut(
        id=role.id, name=role.name, description=role.description,
        user_count=user_count,
    )


def _role_user_count(db: Session, role_id: int) -> int:
    return db.query(User).filter(User.role_id == role_id).count()


@router.get("/roles", response_model=List[RoleOut])
def list_available_roles(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
) -> Any:
    """Catálogo de roles con cuántas cuentas tiene cada uno (Admin only)."""
    counts = dict(
        db.query(User.role_id, func.count(User.id))
        .filter(User.role_id.isnot(None))
        .group_by(User.role_id)
        .all()
    )
    roles = db.query(Role).order_by(Role.name).all()
    return [_role_out(r, counts.get(r.id, 0)) for r in roles]


@router.post("/roles", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
def create_role(
    payload: RoleCreate,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> Any:
    """Crea un rol del catálogo (Admin only). Nace sin permisos de tabla."""
    name = _clean_catalog_name(payload.name, "rol")
    if _catalog_name_taken(db, Role, name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ya existe un rol llamado '{name}'.",
        )

    role = Role(name=name, description=(payload.description or "").strip() or None)
    db.add(role)
    db.commit()
    db.refresh(role)

    _persist_audit_log(
        db=db,
        user_id=current_admin.id,
        username=current_admin.username,
        user_role=display_role_name(current_admin),
        question_prompt=(
            f"Alta de rol '{role.name}' (id={role.id}) por administrador. Nace sin "
            f"permisos de tabla: se asignan en la matriz de permisos."
        ),
        sql_generated=None,
        validation_status="AUTH_ROL_CREADO",
        target_database="metadata",
    )
    return _role_out(role)


@router.patch("/roles/{role_id}", response_model=RoleOut)
def update_role(
    role_id: int,
    payload: RoleUpdate,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> Any:
    """Renombra y/o cambia la descripción de un rol (Admin only).

    Campo ausente = no se toca. `description: null` = se borra la descripción.
    """
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Indica al menos un campo a actualizar: 'name' y/o 'description'.",
        )

    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rol no encontrado.",
        )

    previous_name = role.name
    previous_description = role.description
    changes = []

    if "name" in updates:
        new_name = _clean_catalog_name(payload.name, "rol")
        # El rol administrador se reconoce por nombre exacto en `is_admin_user`.
        # Renombrarlo apagaría el panel para todas sus cuentas de un plumazo.
        if previous_name in ADMIN_ROLES and new_name != previous_name:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"El rol '{previous_name}' es el rol administrador del sistema y "
                    f"no se puede renombrar. Crea otro rol si necesitas otra etiqueta."
                ),
            )
        if _catalog_name_taken(db, Role, new_name, exclude_id=role.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Ya existe un rol llamado '{new_name}'.",
            )
        if new_name != previous_name:
            role.name = new_name

    if "description" in updates:
        role.description = (payload.description or "").strip() or None

    if role.name != previous_name:
        changes.append(f"nombre: '{previous_name}' -> '{role.name}'")
    if role.description != previous_description:
        changes.append("descripción actualizada")

    db.commit()
    db.refresh(role)

    _persist_audit_log(
        db=db,
        user_id=current_admin.id,
        username=current_admin.username,
        user_role=display_role_name(current_admin),
        question_prompt=(
            f"Actualización del rol '{role.name}' (id={role.id}) por administrador: "
            f"{'; '.join(changes) or 'sin cambios efectivos'}."
        ),
        sql_generated=None,
        validation_status="AUTH_ROL_ACTUALIZADO",
        target_database="metadata",
    )
    return _role_out(role, _role_user_count(db, role.id))


@router.delete("/roles/{role_id}")
def delete_role(
    role_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> Any:
    """Borra un rol sin cuentas asignadas (Admin only).

    Sus permisos de tabla/columna y sus links de dominio caen por cascada ORM.
    """
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rol no encontrado.",
        )

    if role.name in ADMIN_ROLES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"El rol '{role.name}' es el rol administrador del sistema y no se "
                f"puede eliminar."
            ),
        )

    user_count = _role_user_count(db, role.id)
    if user_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"El rol '{role.name}' tiene {user_count} usuario(s) asignado(s). "
                f"Reasígnalos a otro rol antes de eliminarlo."
            ),
        )

    deleted_name = role.name
    db.delete(role)
    db.commit()

    _persist_audit_log(
        db=db,
        user_id=current_admin.id,
        username=current_admin.username,
        user_role=display_role_name(current_admin),
        question_prompt=(
            f"Eliminación del rol '{deleted_name}' (id={role_id}) por administrador. "
            f"Sin usuarios asignados; sus permisos de tabla y columna se eliminaron."
        ),
        sql_generated=None,
        validation_status="AUTH_ROL_ELIMINADO",
        target_database="metadata",
    )
    return {"message": f"El rol '{deleted_name}' fue eliminado."}


@router.get("/domains", response_model=List[DomainOut])
def list_domains(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> Any:
    """Dominios de negocio y cuántas entradas del catálogo los usan (Admin only)."""
    counts = dict(
        db.query(SemanticCatalog.domain_id, func.count(SemanticCatalog.id))
        .filter(SemanticCatalog.domain_id.isnot(None))
        .group_by(SemanticCatalog.domain_id)
        .all()
    )
    domains = db.query(Domain).order_by(Domain.name).all()
    return [
        DomainOut(
            id=d.id, name=d.name, description=d.description,
            catalog_entries_count=counts.get(d.id, 0),
        )
        for d in domains
    ]


@router.post("/domains", response_model=DomainOut, status_code=status.HTTP_201_CREATED)
def create_domain(
    payload: DomainCreate,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> Any:
    """Crea un área de negocio para clasificar el catálogo semántico (Admin only)."""
    name = _clean_catalog_name(payload.name, "dominio")
    if _catalog_name_taken(db, Domain, name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ya existe un dominio llamado '{name}'.",
        )

    domain = Domain(name=name, description=(payload.description or "").strip() or None)
    db.add(domain)
    db.commit()
    db.refresh(domain)

    _persist_audit_log(
        db=db,
        user_id=current_admin.id,
        username=current_admin.username,
        user_role=display_role_name(current_admin),
        question_prompt=f"Alta de dominio '{domain.name}' (id={domain.id}) por administrador.",
        sql_generated=None,
        validation_status="AUTH_DOMINIO_CREADO",
        target_database="metadata",
    )
    return DomainOut(id=domain.id, name=domain.name, description=domain.description)


@router.patch("/domains/{domain_id}", response_model=DomainOut)
def update_domain(
    domain_id: int,
    payload: DomainUpdate,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> Any:
    """Renombra y/o cambia la descripción de un dominio (Admin only)."""
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Indica al menos un campo a actualizar: 'name' y/o 'description'.",
        )

    domain = db.query(Domain).filter(Domain.id == domain_id).first()
    if not domain:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dominio no encontrado.",
        )

    previous_name = domain.name
    previous_description = domain.description
    changes = []

    if "name" in updates:
        new_name = _clean_catalog_name(payload.name, "dominio")
        if _catalog_name_taken(db, Domain, new_name, exclude_id=domain.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Ya existe un dominio llamado '{new_name}'.",
            )
        if new_name != previous_name:
            domain.name = new_name

    if "description" in updates:
        domain.description = (payload.description or "").strip() or None

    if domain.name != previous_name:
        changes.append(f"nombre: '{previous_name}' -> '{domain.name}'")
    if domain.description != previous_description:
        changes.append("descripción actualizada")

    db.commit()
    db.refresh(domain)

    catalog_count = db.query(SemanticCatalog).filter(
        SemanticCatalog.domain_id == domain.id
    ).count()
    _persist_audit_log(
        db=db,
        user_id=current_admin.id,
        username=current_admin.username,
        user_role=display_role_name(current_admin),
        question_prompt=(
            f"Actualización del dominio '{domain.name}' (id={domain.id}) por "
            f"administrador: {'; '.join(changes) or 'sin cambios efectivos'}."
        ),
        sql_generated=None,
        validation_status="AUTH_DOMINIO_ACTUALIZADO",
        target_database="metadata",
    )
    return DomainOut(
        id=domain.id, name=domain.name, description=domain.description,
        catalog_entries_count=catalog_count,
    )


@router.delete("/domains/{domain_id}")
def delete_domain(
    domain_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> Any:
    """Borra un dominio (Admin only).

    `semantic_catalog.domain_id` es `ON DELETE SET NULL`: las entradas NO se
    pierden, quedan sin clasificar. El conteo va en la auditoría para que la
    baja no borre clasificación en silencio.
    """
    domain = db.query(Domain).filter(Domain.id == domain_id).first()
    if not domain:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dominio no encontrado.",
        )

    unclassified = db.query(SemanticCatalog).filter(
        SemanticCatalog.domain_id == domain.id
    ).count()
    deleted_name = domain.name
    db.delete(domain)
    db.commit()

    _persist_audit_log(
        db=db,
        user_id=current_admin.id,
        username=current_admin.username,
        user_role=display_role_name(current_admin),
        question_prompt=(
            f"Eliminación del dominio '{deleted_name}' (id={domain_id}) por "
            f"administrador. {unclassified} entrada(s) del catálogo quedaron sin dominio."
        ),
        sql_generated=None,
        validation_status="AUTH_DOMINIO_ELIMINADO",
        target_database="metadata",
    )
    return {
        "message": (
            f"El dominio '{deleted_name}' fue eliminado. "
            f"{unclassified} entrada(s) del catálogo quedaron sin dominio."
        )
    }

@router.get("/users", response_model=List[UserOut])
def list_all_users(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
) -> Any:
    """Lists registered users from metadata database for admin governance (Admin only)."""
    users = db.query(User).all()
    out = []
    for u in users:
        u_out = UserOut.model_validate(u)
        u_out.role_name = display_role_name(u)
        out.append(u_out)
    return out

@router.get("/sessions", response_model=List[SessionOut])
def list_user_sessions(
    user_id: Optional[int] = None,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
) -> Any:
    """Lists active user sessions (Admin only)."""
    query = db.query(UserSession).filter(UserSession.is_revoked == False)
    if user_id:
        query = query.filter(UserSession.user_id == user_id)
    
    sessions = query.order_by(UserSession.last_seen_at.desc()).all()
    result = []
    for s in sessions:
        s_out = SessionOut(
            id=s.id,
            user_id=s.user_id,
            username=s.user.username if s.user else "Desconocido",
            jti=s.jti,
            created_at=s.created_at,
            last_seen_at=s.last_seen_at,
            ip_address=s.ip_address,
            user_agent=s.user_agent,
            is_revoked=s.is_revoked
        )
        result.append(s_out)
    return result

@router.post("/sessions/{session_id}/revoke")
def revoke_session(
    session_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
) -> Any:
    """Revokes an active session immediately (Admin only)."""
    session = db.query(UserSession).filter(UserSession.id == session_id).first()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sesión no encontrada."
        )
    
    session.is_revoked = True
    db.commit()
    return {"message": "Sesión revocada exitosamente."}

@router.patch("/users/{user_id}", response_model=UserOut)
def admin_update_user(
    user_id: int,
    payload: UserRoleUpdate,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> Any:
    """
    Actualiza rol, `is_admin` y/o `is_active` de un usuario (Admin only).

    Devuelve el `UserOut` ya releido de la base: la UI confirma contra esta
    respuesta, no contra un estado local.

    Decisión de diseño — revoca las sesiones del usuario afectado: SÍ, cuando el
    cambio le QUITA acceso: pierde permisos de administrador o la cuenta se
    desactiva. El motivo es el mismo que en `admin_reset_user_password`: el token
    es un JWT de 24h que no lleva el rol ni el estado adentro, así que un token
    emitido con `is_admin=True` (o con la cuenta activa) sigue valiendo hasta
    expirar aunque ya no corresponda. Bajar privilegios o desactivar tiene que
    cortar las sesiones abiertas, no esperar al vencimiento.

    En los demas casos NO se revoca. Subir privilegios no deja nada peligroso
    vivo (el token viejo no podia mas de lo que ya podia), y cambiar el rol sin
    tocar `is_admin` ya surte efecto en la siguiente llamada porque
    `get_current_admin` relee el rol de la base en cada request. Revocar ahi
    botaria a usuarios a los que no se les cambio el acceso. Reactivar tampoco
    revive nada: las sesiones revocadas al desactivar siguen revocadas.
    """
    if payload.role is None and payload.is_admin is None and payload.is_active is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Indica al menos un campo a actualizar: 'role', 'is_admin' y/o 'is_active'.",
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado."
        )

    # Estado previo, para las salvaguardas y para la auditoría (antes/después).
    # El rol del ACTOR se captura antes de aplicar cambios: si el admin se edita
    # a sí mismo, `user` y `current_admin` son la misma instancia de SQLAlchemy y
    # leerlo después registraría el rol nuevo, no el que autorizó la acción.
    previous_role_name = user.role.name if user.role else None
    previous_is_admin = bool(user.is_admin)
    previous_is_active = bool(user.is_active)
    actor_role_name = display_role_name(current_admin)

    new_role = None
    if payload.role is not None:
        role_name = payload.role.strip()
        if not role_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El rol no puede ser una cadena vacía."
            )
        new_role = db.query(Role).filter(Role.name == role_name).first()
        if not new_role:
            available = [r.name for r in db.query(Role).order_by(Role.name).all()]
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"El rol '{role_name}' no existe en el catálogo. Roles disponibles: {', '.join(available)}."
            )

    # --- Salvaguardas de administración -----------------------------------
    # Se calculan sobre el estado RESULTANTE (rol nuevo + is_admin nuevo), no
    # solo sobre `is_admin`: un admin con rol de catálogo también administra.
    had_admin_rights = is_admin_user(user)

    # El estado RESULTANTE usa el rol nuevo si viene, o el actual si no viene.
    # Antes solo miraba `payload.is_admin`/`user.is_admin` y, cuando no se tocaba
    # el rol, un admin que lo era por rol de catálogo (is_admin=False) parecia
    # perder permisos aunque el PATCH solo cambiara is_active.
    effective_is_admin = (
        bool(payload.is_admin) if payload.is_admin is not None else previous_is_admin
    )
    effective_role_name = new_role.name if new_role is not None else previous_role_name
    keeps_admin_rights = effective_is_admin or (effective_role_name in ADMIN_ROLES)

    losing_admin_rights = had_admin_rights and not keeps_admin_rights

    if losing_admin_rights and user.id == current_admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No puedes quitarte a ti mismo los permisos de administrador. Pídele el cambio a otro administrador."
        )

    if losing_admin_rights:
        remaining_admins = [
            u for u in db.query(User).filter(User.id != user.id).all()
            if u.is_active and is_admin_user(u)
        ]
        if not remaining_admins:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No se puede quitar los permisos de administrador al último administrador del sistema."
            )

    # --- Salvaguardas de desactivación ------------------------------------
    # Solo se validan en la transición activo -> inactivo: repetir
    # `is_active=False` sobre una cuenta ya inactiva no cambia nada y bloquearlo
    # haría imposible editar el rol de una cuenta desactivada.
    deactivating = payload.is_active is False and previous_is_active

    if deactivating and user.id == current_admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No puedes desactivar tu propia cuenta. Pídele el cambio a otro administrador."
        )

    if deactivating and is_admin_user(user):
        remaining_admins = [
            u for u in db.query(User).filter(User.id != user.id).all()
            if u.is_active and is_admin_user(u)
        ]
        if not remaining_admins:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No se puede desactivar al último administrador activo del sistema."
            )

    # --- Aplicación --------------------------------------------------------
    if new_role is not None:
        user.role_id = new_role.id
    if payload.is_admin is not None:
        user.is_admin = payload.is_admin
    if payload.is_active is not None:
        user.is_active = payload.is_active

    # Desactivar corta el acceso igual que perder privilegios: un token vivo de
    # una cuenta desactivada no puede seguir trabajando. Reactivar no revive
    # ninguna sesión: las revocadas quedan revocadas y el usuario vuelve a
    # loguearse.
    if losing_admin_rights or deactivating:
        db.query(UserSession).filter(
            UserSession.user_id == user.id,
            UserSession.is_revoked == False
        ).update({"is_revoked": True})

    db.commit()
    db.refresh(user)

    changes = []
    if new_role is not None:
        changes.append(f"rol: '{previous_role_name}' -> '{new_role.name}'")
    if payload.is_admin is not None:
        changes.append(f"is_admin: {previous_is_admin} -> {bool(payload.is_admin)}")
    if payload.is_active is not None:
        changes.append(f"is_active: {previous_is_active} -> {bool(payload.is_active)}")

    # Sin secretos: solo estado de gobernanza (rol, flag de admin, alta/baja).
    _persist_audit_log(
        db=db,
        user_id=current_admin.id,
        username=current_admin.username,
        user_role=actor_role_name,
        question_prompt=(
            f"Actualización de la cuenta '{user.username}' (id={user.id}) por "
            f"administrador: {'; '.join(changes)}."
        ),
        sql_generated=None,
        validation_status=(
            "AUTH_DESACTIVACION_CUENTA" if deactivating else "AUTH_ACTUALIZACION_USUARIO"
        ),
        target_database="metadata",
    )

    user_out = UserOut.model_validate(user)
    user_out.role_name = display_role_name(user)
    return user_out

@router.post("/users/{user_id}/revoke-all-sessions")
def revoke_all_user_sessions(
    user_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
) -> Any:
    """Revokes all active sessions for a target user (Admin only)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado."
        )

    count = db.query(UserSession).filter(
        UserSession.user_id == user_id,
        UserSession.is_revoked == False
    ).update({"is_revoked": True})
    db.commit()

    return {"message": f"Se revocaron {count} sesión(es) activa(s) del usuario {user.username}."}

@router.post("/users/{user_id}/reset-password", response_model=PasswordResetResponse)
def admin_reset_user_password(
    user_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
) -> Any:
    """
    Generates a secure temporary password, sets must_change_password=True,
    clears lockout state, and revokes all active sessions (Admin only).
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado."
        )

    temp_password = _generate_temp_password()
    now = utcnow_naive()
    user.hashed_password = get_password_hash(temp_password)
    user.must_change_password = True
    user.temp_password_issued_at = now
    user.failed_login_attempts = 0
    user.locked_until = None

    # Revoke active sessions for target user
    db.query(UserSession).filter(
        UserSession.user_id == user_id,
        UserSession.is_revoked == False
    ).update({"is_revoked": True})

    db.commit()

    _persist_audit_log(
        db=db,
        user_id=current_admin.id,
        username=current_admin.username,
        user_role=display_role_name(current_admin),
        question_prompt=f"Reset de contraseña de la cuenta '{user.username}' por administrador.",
        sql_generated=None,
        validation_status="AUTH_RESET_CLAVE",
        target_database="metadata",
    )

    return PasswordResetResponse(
        message=f"Contraseña de '{user.username}' restablecida exitosamente. Comunica esta contraseña temporal al usuario.",
        username=user.username,
        temporary_password=temp_password,
        temp_password_expires_at=user.temp_password_expires_at,
    )

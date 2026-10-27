import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, ConfigDict, Field

MIN_PASSWORD_LENGTH = 6

def validate_password_strength(raw: str) -> str:
    """
    Única regla de contraseña de la aplicación.

    Vive acá y no en cada router porque el alta pública de cuentas (ya
    eliminada) aceptaba `password="a"` con 201 mientras /auth/change-password
    exigía 6 caracteres a cuatro líneas de distancia: quedaba una credencial de
    1 carácter que después ni podía normalizarse. Hoy las claves temporales las
    genera el servidor; esta regla sigue siendo la del cambio de contraseña.
    """
    cleaned = (raw or "").strip()
    if len(cleaned) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres.")
    return cleaned

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"

class UserLogin(BaseModel):
    username: str
    password: str

class UserAdminCreate(BaseModel):
    """Body de `POST /auth/users` (admin only).

    La contraseña NO la elige el administrador ni el usuario: la genera el
    servidor y se devuelve una sola vez. Por eso este schema no tiene
    `password`, y los campos extra (`is_admin`, `role_id`, `password`) que un
    cliente intente colar se ignoran.
    """
    username: str = Field(..., min_length=1, max_length=50)
    email: Optional[EmailStr] = None

class UserRoleUpdate(BaseModel):
    """
    Body de `PATCH /auth/users/{user_id}` (admin only).

    Los campos son opcionales para poder cambiar solo uno, y `None` significa
    "no lo toques". Por eso `{}` NO es un cambio: el router lo rechaza con 400 en vez
    de devolver 200 sin haber modificado nada. `role` es el NOMBRE del rol del
    catalogo (`/auth/roles`), no un id suelto: un string que no matchea ningun rol
    no debe poder guardarse. `is_active=False` desactiva la cuenta (login y toda
    sesion abierta quedan cortados); `True` la reactiva sin revivir sesiones.
    """
    role: Optional[str] = None
    is_admin: Optional[bool] = None
    is_active: Optional[bool] = None

class UserOut(BaseModel):
    id: int
    username: str
    email: Optional[str] = None
    is_admin: bool
    is_active: bool
    role_id: Optional[int] = None
    role_name: Optional[str] = None
    must_change_password: bool = False
    temp_password_issued_at: Optional[datetime.datetime] = None
    temp_password_expires_at: Optional[datetime.datetime] = None
    failed_login_attempts: int = 0
    locked_until: Optional[datetime.datetime] = None

    model_config = ConfigDict(from_attributes=True)

class UserCreationResponse(BaseModel):
    message: str
    temporary_password: str
    user: UserOut

class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str

class PasswordResetResponse(BaseModel):
    message: str
    username: str
    temporary_password: str
    temp_password_expires_at: Optional[datetime.datetime] = None

class SessionOut(BaseModel):
    id: int
    user_id: int
    username: Optional[str] = None
    jti: str
    created_at: datetime.datetime
    last_seen_at: datetime.datetime
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    is_revoked: bool

    model_config = ConfigDict(from_attributes=True)


# --- Catálogo de roles y dominios (solo admin) ------------------------------
#
# `name` es la etiqueta que se muestra y con la que se asigna el rol a un
# usuario (`PATCH /auth/users`), así que se valida acá y se compara sin
# distinguir mayúsculas en el router. `description` es opcional y `null`
# significa "sin descripción", no "no tocar": en un PATCH el campo ausente es
# el que significa "no tocar".

class RoleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    description: Optional[str] = Field(None, max_length=500)

class RoleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    description: Optional[str] = Field(None, max_length=500)

class RoleOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    # Cuántas cuentas tienen el rol asignado. La UI lo necesita para explicar
    # POR QUÉ el borrado va a fallar antes de ofrecer el botón, y el router lo
    # usa como condición de borrado (409 con usuarios asignados).
    user_count: int = 0

class DomainCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    description: Optional[str] = Field(None, max_length=500)

class DomainUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    description: Optional[str] = Field(None, max_length=500)

class DomainOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    # Entradas del catálogo semántico adscritas al dominio. Al borrar, la FK
    # `semantic_catalog.domain_id` las deja en NULL: no se pierden, pierden la
    # clasificación. La UI lo muestra antes de confirmar.
    catalog_entries_count: int = 0

Token.model_rebuild()

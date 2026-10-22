import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from app.core.config import settings
from app.core.database import Base
from app.core.time import utcnow_naive

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=True)
    hashed_password = Column(String(255), nullable=False)
    is_admin = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    
    # Account lockout & Security policies
    failed_login_attempts = Column(Integer, default=0, nullable=False)
    locked_until = Column(DateTime, nullable=True)
    must_change_password = Column(Boolean, default=False, nullable=False)
    # Momento en que se emitio la clave temporal. Se limpia al cambiarla. Una
    # cuenta creada por un admin puede operar con la temporal durante
    # TEMP_PASSWORD_TTL_DAYS; despues, `get_current_user` la obliga a cambiarla.
    temp_password_issued_at = Column(DateTime, nullable=True)
    
    role_id = Column(Integer, ForeignKey("roles.id", ondelete="SET NULL"), nullable=True)
    
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

    role = relationship("Role", back_populates="users")
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")

    @property
    def temp_password_expires_at(self):
        """Fecha de vencimiento de la clave temporal, o None si no hay una."""
        if not self.temp_password_issued_at:
            return None
        return self.temp_password_issued_at + datetime.timedelta(days=settings.TEMP_PASSWORD_TTL_DAYS)


class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, index=True, nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow_naive)

    users = relationship("User", back_populates="role")
    domain_links = relationship("RoleDomainLink", back_populates="role", cascade="all, delete-orphan")
    table_permissions = relationship("RoleTablePermission", back_populates="role", cascade="all, delete-orphan")
    column_permissions = relationship("RoleColumnPermission", back_populates="role", cascade="all, delete-orphan")


class Domain(Base):
    __tablename__ = "domains"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, index=True, nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow_naive)

    role_links = relationship("RoleDomainLink", back_populates="domain", cascade="all, delete-orphan")


class UserSession(Base):
    """
    Tracks active user sessions with JWT JTI claim for revocation,
    device auditing and forced session termination.
    """
    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    jti = Column(String(64), unique=True, index=True, nullable=False)
    
    created_at = Column(DateTime, default=utcnow_naive, nullable=False)
    last_seen_at = Column(DateTime, default=utcnow_naive, nullable=False)
    
    ip_address = Column(String(50), nullable=True)
    user_agent = Column(String(255), nullable=True)
    
    is_revoked = Column(Boolean, default=False, nullable=False, index=True)

    user = relationship("User", back_populates="sessions")

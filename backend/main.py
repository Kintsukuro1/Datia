import uvicorn
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.logging import logger
from app.api.v1.router import api_router
from app.core.database import SessionLocal
from app.db.init_db import init_db

# `/docs` y el esquema OpenAPI no piden autenticacion: describen los 50 endpoints
# con todos sus parametros a cualquiera que alcance el puerto. En produccion van
# apagados salvo que `ENABLE_API_DOCS` los vuelva a encender a proposito.
_IS_PROD = settings.ENVIRONMENT.lower() == "production"
_DOCS_ENABLED = settings.ENABLE_API_DOCS if settings.ENABLE_API_DOCS is not None else not _IS_PROD

def validate_startup_security():
    """Enforces production checks and warns about risky local configurations."""
    import sys

    def _warn(message: str) -> None:
        # stderr y no logger: el nivel INFO/ERROR de core/logging.py puede tapar
        # un warning que solo se lee una vez, al arrancar.
        print(f"\n  [SEGURIDAD] {message}\n", file=sys.stderr, flush=True)

    is_prod = settings.ENVIRONMENT.lower() == "production"

    if is_prod:
        if settings.SECRET_KEY == settings.DEFAULT_SECRET_KEY:
            raise RuntimeError(
                "SECRET_KEY no fue configurado para producción. Defina una clave única en su archivo .env antes de desplegar."
            )
        if "*" in settings.CORS_ORIGINS:
            # `allow_credentials=False` reduce el daño, pero un origen `*` sigue
            # permitiendo que cualquier sitio haga peticiones a la API. No se
            # detiene el arranque: es una advertencia, no una condición fatal
            # como la SECRET_KEY por defecto.
            _warn(
                "CORS con origen '*' en producción. Cualquier sitio puede llamar "
                "a los endpoints públicos; la sesión viaja en el header Bearer, "
                "no en cookies, pero conviene restringir CORS_ORIGINS a los "
                "orígenes reales."
            )
            logger.error(
                "CORS: origenes abiertos con '*' en produccion. "
                "Defina CORS_ORIGINS con los origenes reales."
            )
        if settings.ENABLE_API_DOCS:
            _warn("/docs y OpenAPI están habilitados en producción sin autenticación.")
        if settings.DEMO_MODE_EFFECTIVE:
            _warn(
                "DEMO_MODE está activo en producción: hay cuentas con contraseñas "
                "conocidas (admin/admin123 y otras del README)."
            )
    elif settings.SECRET_KEY == settings.DEFAULT_SECRET_KEY:
        _warn(
            "SECRET_KEY por defecto en desarrollo. No use esta clave en una "
            "instalación accesible por otra máquina."
        )

    if not settings.FERNET_KEY:
        # No se genera una clave aleatoria: se deriva de SECRET_KEY con sha256
        # (core/security.py). Por eso el riesgo no es "cada reinicio" sino
        # rotar SECRET_KEY: ahi las credenciales guardadas dejan de abrirse.
        _warn(
            "FERNET_KEY no configurada: se deriva de SECRET_KEY. Si SECRET_KEY "
            "cambia, las credenciales cifradas de los conectores dejarán de "
            "descifrarse. Defina FERNET_KEY en .env (ver .env.example)."
        )

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Arranque del servidor. Reemplaza a `@app.on_event("startup")`, deprecado
    por FastAPI; el mecanismo vigente es el lifespan."""
    logger.info("Iniciando Servidor Backend FastAPI...")
    validate_startup_security()

    # 1. Ensure PostgreSQL databases, schemas, and base data exist (Auto-healing)
    try:
        from scripts.setup_postgres_full import run as run_postgres_full_setup
        run_postgres_full_setup()
    except Exception as e_pg:
        logger.warning(f"Aviso durante auto-inicialización de PostgreSQL: {e_pg}")

    from app.core.database import engine
    logger.info(f"Motor de base de datos activo: {engine.dialect.name.upper()} ({engine.url.render_as_string(hide_password=True)})")

    # 2. Initialize App Metadata (Users, Roles, RBAC permissions, Connectors)
    try:
        db = SessionLocal()
        init_db(db)
        db.close()
        logger.info("Base de datos de metadatos e identidades inicializada correctamente.")
    except Exception as e:
        logger.warning(f"Aviso de inicio de base de datos: {str(e)}. El sistema operará en modo contingencia.")

    yield

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json" if _DOCS_ENABLED else None,
    docs_url="/docs" if _DOCS_ENABLED else None,
    redoc_url="/redoc" if _DOCS_ENABLED else None,
    description="Backend Sidecar Python para Democratización de Datos Corporativos con IA Local",
    lifespan=lifespan,
)

# CORS configuration for Vite dev server & Electron desktop app.
# La autenticacion va en el header Authorization, no en cookies: no hace falta
# `allow_credentials=True`. Con `*` en CORS_ORIGINS, habilitarlo ademas haria
# que el middleware refleje cualquier origen.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API v1 router
app.include_router(api_router, prefix=settings.API_V1_STR)

@app.get("/")
def root():
    return {
        "status": "online",
        "app": settings.PROJECT_NAME,
        "mode": "100% Offline Standalone",
        "docs": "/docs"
    }

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "app": settings.PROJECT_NAME,
        "environment": settings.ENVIRONMENT
    }

if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=True)

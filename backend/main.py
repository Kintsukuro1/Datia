import uvicorn
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

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json" if _DOCS_ENABLED else None,
    docs_url="/docs" if _DOCS_ENABLED else None,
    redoc_url="/redoc" if _DOCS_ENABLED else None,
    description="Backend Sidecar Python para Democratización de Datos Corporativos con IA Local"
)

# CORS configuration for Vite dev server & Electron desktop app
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def validate_startup_security():
    """Enforces production environment security checks before starting."""
    if settings.ENVIRONMENT.lower() == "production":
        if settings.SECRET_KEY == settings.DEFAULT_SECRET_KEY:
            raise RuntimeError(
                "SECRET_KEY no fue configurado para producción. Defina una clave única en su archivo .env antes de desplegar."
            )
        if "*" in settings.CORS_ORIGINS:
            # `allow_credentials=True` con origen `*` hace que el middleware
            # repita el origen de la petición, o sea que CUALQUIER sitio que el
            # usuario visite puede llamar a la API con su sesion.
            #
            # No se detiene el arranque a proposito: casi toda instalacion local
            # tiene ENVIRONMENT=production en su `.env` (asi se ejercita el guard
            # de SECRET_KEY) y romperlas seria peor que avisar. Por eso va a
            # stderr y no al log: un `logger.warning` queda tapado por el nivel de
            # INFO de `core/logging.py` y nadie lo lee nunca.
            import sys
            aviso = (
                "\n"
                "  [SEGURIDAD] CORS con origen '*' y credenciales habilitadas.\n"
                "  Cualquier sitio web que visite un usuario autenticado puede\n"
                "  llamar a esta API con su sesion. Defina en su .env:\n"
                "      CORS_ORIGINS=[\"http://<host-del-frontend>\"]\n"
                "  y no '*'.\n"
            )
            print(aviso, file=sys.stderr, flush=True)
            logger.error(
                "CORS: origenes abiertos con '*' en produccion. "
                "Defina CORS_ORIGINS con los origenes reales."
            )

# Include API v1 router
app.include_router(api_router, prefix=settings.API_V1_STR)

@app.on_event("startup")
def on_startup():
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

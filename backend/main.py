import uvicorn
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from app.core.config import settings
from app.core.logging import logger
from app.api.v1.router import api_router
from app.core.database import SessionLocal

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
        if settings.LLM_MODE_EFFECTIVE == "user":
            _warn(
                "LLM_MODE=user en producción: cualquier usuario autenticado puede "
                "apuntar el motor LLM a un host de la red privada. Habilítelo solo "
                "si esa red está segmentada y confía en sus usuarios."
            )
        # En un backend dentro de contenedor, localhost es el propio contenedor:
        # Ollama no está ahí. Es una advertencia y no un error porque un backend
        # corriendo directo en el host sí usa loopback.
        from urllib.parse import urlsplit
        llm_host = (urlsplit(settings.OLLAMA_BASE_URL).hostname or "").lower()
        if llm_host in {"localhost", "127.0.0.1", "::1"}:
            _warn(
                "OLLAMA_BASE_URL apunta al loopback. Si el backend corre en un "
                "contenedor, use el servicio 'ollama' del compose o la IP del host; "
                "localhost sería el propio contenedor."
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
    pg_ready = False
    try:
        from scripts.setup_postgres_full import run as run_postgres_full_setup
        pg_ready = run_postgres_full_setup()
    except Exception as e_pg:
        if _IS_PROD:
            # En produccion no se arranca a medias: el compose espera
            # /health/ready y el operador ve el error real, no un 200 mentiroso.
            raise
        logger.warning(f"Aviso durante auto-inicialización de PostgreSQL: {e_pg}")

    if _IS_PROD and not pg_ready:
        raise RuntimeError(
            "PostgreSQL no está disponible: no se puede inicializar la metadata. "
            "Revise POSTGRES_* y el servicio postgres antes de reintentar."
        )

    from app.core.database import engine
    logger.info(f"Motor de base de datos activo: {engine.dialect.name.upper()} ({engine.url.render_as_string(hide_password=True)})")

    # `run_postgres_full_setup()` ya llama a `init_db` (unico seeder de
    # identidad, RBAC y conector). Llamarlo otra vez aca era un segundo
    # arranque del mismo seeder en cada inicio.

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

@app.get("/health/live")
def liveness_check():
    """Proceso vivo. No toca dependencias: es lo que usa un orquestador para
    decidir si reiniciar el contenedor."""
    return {"status": "alive"}

@app.get("/health/ready")
def readiness_check():
    """Proceso listo para atender. Verifica la metadata; si no responde, 503.

    No devuelve el error de SQLAlchemy: el detalle queda en el log del server y
    el healthcheck solo necesita el estado. Los datos de conexion no viajan.
    """
    db = None
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception as exc:
        logger.warning(f"Readiness check falló: {exc}")
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    finally:
        if db is not None:
            db.close()

if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=True)

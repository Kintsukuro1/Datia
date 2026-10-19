import os
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

def _backend_dir() -> str:
    """Directory holding the backend package (…/backend)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def _root_dir() -> str:
    """Project root holding data_sources/.

    Derived from the checkout layout normally, so a fresh clone works with no
    configuration. In Docker the backend is copied to /app, whose parent is /,
    which would put data_sources outside the mounted volume and lose every
    uploaded database on recreate — so the image pins DATIA_ROOT=/app.
    """
    return os.environ.get("DATIA_ROOT") or os.path.dirname(_backend_dir())

class Settings(BaseSettings):
    PROJECT_NAME: str = "Democratización de Datos Corporativos con IA Local"
    API_V1_STR: str = "/api/v1"
    
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    CORS_ORIGINS: List[str] = ["*"]
    
    ENVIRONMENT: str = "development" # development | production

    # Publica `/docs` y `/api/v1/openapi.json`. Sin autenticacion: describen los
    # 50 endpoints con todos sus parametros y esquemas a cualquiera que alcance
    # el puerto. Por eso en produccion viene apagado y hay que encenderlo a mano.
    #
    # Notado: con ENVIRONMENT=production y esto ausente, `/docs` devuelve 404.
    # Para recuperarlo en local, pon ENABLE_API_DOCS=true en el `.env`.
    ENABLE_API_DOCS: Optional[bool] = None

    # Siembra de la demo al arrancar (`init_db` y `setup_postgres_full`): usuarios
    # con clave conocida (admin/admin123 y las demas del README), la base de
    # negocio de demostracion, su conector y la matriz de permisos que la
    # sustenta. En una instalacion real eso deja cuentas ACTIVAS con claves
    # publicadas, asi que el default es apagado en production. Una maquina demo
    # lo prende explicito con DEMO_MODE=true en su `.env` (docker-compose lo
    # interpola).
    DEMO_MODE: Optional[bool] = None

    @property
    def DEMO_MODE_EFFECTIVE(self) -> bool:
        """Sin valor explicito: encendido fuera de production. Mismo criterio que ENABLE_API_DOCS."""
        if self.DEMO_MODE is not None:
            return self.DEMO_MODE
        return self.ENVIRONMENT.lower() != "production"

    DEFAULT_SECRET_KEY: str = "democratizacion_datos_super_secret_key_local_2026_aes256_change_in_prod"

    # Secret Key for JWT Tokens and AES Encryption
    SECRET_KEY: str = "democratizacion_datos_super_secret_key_local_2026_aes256_change_in_prod"
    FERNET_KEY: Optional[str] = None # Auto-generated if not set
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 # 24 hours local session
    
    # Internal Metadata Database (PostgreSQL)
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "democratizacion_metadatos"
    
    # Local LLM Default Settings
    LLM_PROVIDER: str = "ollama" # ollama | openai_compatible | custom
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5-coder:7b"
    OPENAI_COMPATIBLE_URL: str = "http://localhost:8000/v1"
    OPENAI_COMPATIBLE_API_KEY: str = "lm-studio"

    # Ventana de contexto por request. Antes era el literal 16384 repetido en
    # tres lugares de `llm_service.py`. Qwen2.5-Coder-7B tiene 32K nativos
    # (131K con YaRN), asi que 16K desperdiciaba la mitad sin motivo.
    #
    # Honesto sobre el alcance: solo lo aplica el servidor que lo acepta por
    # request. llama.cpp lo fija al arrancar (`llama-server -c 32768`) y LM
    # Studio lo ignora; en ambos hace falta arrancar el servidor con la
    # ventana que se quiera de verdad.
    LLM_NUM_CTX: int = 32768

    # --- Enriquecimiento de catalogo con IA (knobs de hardware) --------------
    # Una llamada al modelo por LOTE de columnas (no por tabla entera ni por
    # columna). El lote chico evita los dos cortes que perdian la tabla completa:
    # truncado por `max_tokens` (JSON sin cerrar = inusable) y timeout por
    # llamada en CPU lenta. El tamano efectivo del lote ademas baja solo, con la
    # velocidad medida del modelo (ver `CatalogEnricher._columns_per_call`):
    # estos valores son el techo y los fences.
    LLM_ENRICH_COLUMNS_PER_CALL: int = 6
    LLM_ENRICH_TABLE_TIMEOUT_S: float = 120.0
    LLM_ENRICH_TOTAL_BUDGET_S: float = 360.0
    
    # Security Defaults
    DEFAULT_ROW_LIMIT: int = 500
    QUERY_TIMEOUT_SECONDS: int = 15

    # Path to Uploaded and Structured Data Sources
    @property
    def DATA_SOURCES_DIR(self) -> str:
        p = os.path.join(_root_dir(), "data_sources")
        os.makedirs(p, exist_ok=True)
        return p


    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )

settings = Settings()

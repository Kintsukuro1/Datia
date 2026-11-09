import json
import logging
import sys
from datetime import datetime, timezone

from app.core.request_context import get_request_id


class RequestIdFilter(logging.Filter):
    """Agrega `request_id` a cada registro, venga o no del middleware."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _use_json() -> bool:
    from app.core.config import settings

    mode = (settings.LOG_FORMAT or "auto").lower()
    if mode == "json":
        return True
    if mode == "text":
        return False
    return settings.ENVIRONMENT.lower() == "production"


def setup_logging():
    logger = logging.getLogger("democratizacion_backend")
    logger.setLevel(logging.INFO)

    if _use_json():
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] [%(request_id)s] [%(name)s]: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    # Console handler
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(RequestIdFilter())

    if not logger.handlers:
        logger.addHandler(stream_handler)

    # Sin esto cada linea sale dos veces cuando otro modulo ya configuro el root
    # (`setup_postgres_full` llama a `logging.basicConfig` al importarse): una vez
    # por este handler y otra por el del root.
    logger.propagate = False

    return logger


logger = setup_logging()

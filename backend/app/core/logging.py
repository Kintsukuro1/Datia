import logging
import sys

def setup_logging():
    logger = logging.getLogger("democratizacion_backend")
    logger.setLevel(logging.INFO)
    
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # Console handler
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    
    if not logger.handlers:
        logger.addHandler(stream_handler)

    # Sin esto cada linea sale dos veces cuando otro modulo ya configuro el root
    # (`setup_postgres_full` llama a `logging.basicConfig` al importarse): una vez
    # por este handler y otra por el del root.
    logger.propagate = False

    return logger

logger = setup_logging()

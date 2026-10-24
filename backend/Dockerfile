# =============================================================================
# Backend de Dat.ia - imagen de produccion multi-stage, sin toolchain ni root.
#
# El contexto de build es ./backend. La metadata y los datos del cliente NO van
# en la imagen: PostgreSQL y el volumen data_sources viven fuera.
# =============================================================================
FROM python:3.13-slim AS builder

WORKDIR /build

# Toolchain solo para compilar dependencias que no traigan wheel. La imagen
# final no lo hereda.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# ponytail: requirements.txt incluye pytest/hypothesis porque es una sola lista.
# Separa un requirements-runtime.txt cuando el tamaño de la imagen importe.
RUN pip install --no-cache-dir --upgrade pip && \
    pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt


FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    DATIA_ROOT=/app \
    HOST=0.0.0.0 \
    PORT=8000

# libpq5 y curl son runtime: el driver psycopg y el healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/* && \
    rm -rf /wheels

WORKDIR /app

# COPY explicito, no `COPY . .`: la imagen no lleva tests, .env, venv ni datos.
COPY app ./app
COPY main.py ./
COPY scripts ./scripts

# El proceso no corre como root. El volumen data_sources se inicializa con el
# owner de esta carpeta cuando esta vacio; upgrade.sh corrige el owner de un
# volumen viejo creado por una imagen anterior que corria como root.
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin datia && \
    mkdir -p /app/data_sources && \
    chown -R datia:datia /app

USER datia

EXPOSE 8000

# readiness, no liveness: un contenedor que arranco pero no alcanza su metadata
# no debe recibir trafico. start-period amplio porque el primer arranque crea
# esquema y siembra roles.
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8000/health/ready || exit 1

# El backend no publica puerto: solo lo alcanza nginx por la red de compose.
# `--forwarded-allow-ips=*` es seguro en ese layout y permite que la auditoria
# registre la IP real que nginx pone en X-Forwarded-For.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]

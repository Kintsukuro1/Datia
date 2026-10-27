#!/usr/bin/env sh
# Instalacion inicial: crea .env con secretos aleatorios, levanta el stack,
# descarga el modelo del Ollama central y crea el primer administrador.
#
# No es idempotente con datos: si .env ya existe, lo respeta. Para reinstalar
# desde cero en una maquina de prueba: docker compose down -v y borre .env.
set -eu
. ./lib.sh

command -v openssl >/dev/null 2>&1 || fail "Falta openssl: setup.sh lo usa para generar los secretos y la clave del admin."

if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env 2>/dev/null || true

    _secret=$(openssl rand -hex 32)
    _fernet=$(openssl rand -base64 32 | tr '+/' '-_')
    _pgpass=$(openssl rand -hex 24)

    replace_env SECRET_KEY "$_secret"
    replace_env FERNET_KEY "$_fernet"
    replace_env POSTGRES_PASSWORD "$_pgpass"

    # El bundle de release incluye VERSION. Fija las imagenes a ese tag; si no
    # existe (prueba local), deja :latest y preflight lo advierte.
    if [ -f VERSION ]; then
        _version=$(tr -d '[:space:]' < VERSION)
        if [ -n "$_version" ]; then
            replace_env DATIA_BACKEND_IMAGE "ghcr.io/kintsukuro1/datia-backend:$_version"
            replace_env DATIA_FRONTEND_IMAGE "ghcr.io/kintsukuro1/datia-frontend:$_version"
        fi
    fi
    printf 'Creado .env con secretos nuevos.\n'
else
    if grep -Eq '^(SECRET_KEY|FERNET_KEY|POSTGRES_PASSWORD)=CHANGE_ME' .env; then
        fail ".env existe y todavia tiene placeholders. Edite los secretos o borre .env (y los volumenes, si es una instalacion nueva) y vuelva a correr ./setup.sh."
    fi
    printf 'Usando .env existente.\n'
fi

./preflight.sh

printf 'Descargando imagenes...\n'
if [ "${DATIA_SKIP_PULL:-$(env_get DATIA_SKIP_PULL || true)}" = "1" ]; then
    warn "DATIA_SKIP_PULL=1: se usan las imagenes locales (instalacion air-gapped)."
else
    docker compose pull || fail "No se pudieron descargar las imagenes. Si los paquetes de GHCR son privados: echo <PAT-con-read:packages> | docker login ghcr.io -u <usuario> --password-stdin"
fi

printf 'Levantando servicios...\n'
docker compose up -d --wait --wait-timeout 300

OLLAMA_MODEL=$(env_get OLLAMA_MODEL || true)
[ -n "$OLLAMA_MODEL" ] || OLLAMA_MODEL="qwen2.5-coder:7b"

# El servicio `ollama` de este compose solo se usa si OLLAMA_BASE_URL apunta a
# el. En `external` (Ollama en el host/LAN) descargar el modelo aca seria
# desperdiciar disco y, peor, hacer fallar la instalacion por un motor que no
# se va a usar.
OLLAMA_BASE_URL=$(strip_quotes "$(env_get OLLAMA_BASE_URL || true)")
_local_ollama=0
case "$OLLAMA_BASE_URL" in
    ""|http://ollama:11434|http://ollama:11434/*) _local_ollama=1 ;;
esac

SKIP_MODEL_PULL=${DATIA_SKIP_MODEL_PULL:-$(env_get DATIA_SKIP_MODEL_PULL || true)}
if [ "$_local_ollama" != "1" ]; then
    warn "OLLAMA_BASE_URL apunta a un motor externo ($OLLAMA_BASE_URL); no se descarga ni verifica el modelo en el contenedor local."
elif [ "$SKIP_MODEL_PULL" = "1" ]; then
    warn "DATIA_SKIP_MODEL_PULL=1: no se descargo '$OLLAMA_MODEL'. Carguelo antes de usar el chat."
else
    printf "Descargando modelo '%s' (puede tardar varios minutos)...\n" "$OLLAMA_MODEL"
    if ! docker compose exec -T ollama ollama pull "$OLLAMA_MODEL"; then
        warn "No se pudo descargar '$OLLAMA_MODEL'. La instalacion esta arriba, pero el motor LLM no respondera hasta que el modelo exista:"
        warn "  docker compose exec ollama ollama pull $OLLAMA_MODEL"
    fi
fi

ADMIN_USERNAME=${DATIA_ADMIN_USERNAME:-$(env_get DATIA_ADMIN_USERNAME || true)}
[ -n "$ADMIN_USERNAME" ] || ADMIN_USERNAME=admin
ADMIN_EMAIL=${DATIA_ADMIN_EMAIL:-$(env_get DATIA_ADMIN_EMAIL || true)}
ADMIN_PASSWORD=${DATIA_ADMIN_PASSWORD:-$(env_get DATIA_ADMIN_PASSWORD || true)}
_generated_password=0

if [ -z "$ADMIN_PASSWORD" ]; then
    ADMIN_PASSWORD=$(openssl rand -base64 24 | tr -d '\n' | tr '+/' '-_' | tr -d '=')
    _generated_password=1
fi

printf 'Creando primer administrador...\n'
set +e
if [ -n "$ADMIN_EMAIL" ]; then
    _admin_out=$(docker compose exec -T -e DATIA_ADMIN_PASSWORD="$ADMIN_PASSWORD" backend \
        python -m scripts.create_admin --username "$ADMIN_USERNAME" --email "$ADMIN_EMAIL" 2>&1)
else
    _admin_out=$(docker compose exec -T -e DATIA_ADMIN_PASSWORD="$ADMIN_PASSWORD" backend \
        python -m scripts.create_admin --username "$ADMIN_USERNAME" 2>&1)
fi
_admin_rc=$?
set -e
printf '%s\n' "$_admin_out"

if [ "$_admin_rc" -eq 2 ] && printf '%s' "$_admin_out" | grep -Eq "ya existe|ya esta registrado"; then
    warn "El admin '$ADMIN_USERNAME' ya existe; no se modifico su clave."
elif [ "$_admin_rc" -ne 0 ]; then
    fail "No se pudo crear el admin. La instalacion esta arriba, pero no se puede entrar hasta resolverlo."
elif [ "$_generated_password" -eq 1 ]; then
    printf '\n'
    printf '  ============================================================\n'
    printf '  Admin:          %s\n' "$ADMIN_USERNAME"
    printf '  Clave inicial:  %s\n' "$ADMIN_PASSWORD"
    printf '  Se debe cambiar en el primer login (must_change_password).\n'
    printf '  ============================================================\n'
    printf '\n'
else
    printf 'Admin creado con la clave definida en el entorno o en .env.\n'
fi

DATIA_BIND_IP=$(strip_quotes "$(env_get DATIA_BIND_IP || true)")
DATIA_HTTP_PORT=$(env_get DATIA_HTTP_PORT || true)
PROFILES=${COMPOSE_PROFILES:-$(env_get COMPOSE_PROFILES || true)}

printf '\nDatia esta arriba.\n'
if [ "$DATIA_BIND_IP" = "127.0.0.1" ]; then
    printf '  URL local: http://127.0.0.1:%s\n' "$DATIA_HTTP_PORT"
else
    printf '  URL: http://<IP-del-host>:%s (publicado en %s)\n' "$DATIA_HTTP_PORT" "$DATIA_BIND_IP"
fi
case ",$PROFILES," in
    *,caddy,*) printf '  TLS:      https://%s\n' "$(env_get DATIA_DOMAIN || true)" ;;
esac
printf '\n  Estado:   docker compose ps\n'
printf '  Logs:     docker compose logs -f backend\n'
printf '  Backup:   ./backup.sh\n'
printf '\n'

# Verificacion final del modelo: un `ollama pull` que fallo a mitad deja el
# stack arriba y el chat muerto. Si el operador eligio no descargar (air-gapped),
# la responsabilidad es suya y no se falla.
if [ "$_local_ollama" = "1" ] && [ "$SKIP_MODEL_PULL" != "1" ]; then
    if ! docker compose exec -T ollama ollama list | awk -v m="$OLLAMA_MODEL" 'NR > 1 && ($1 == m || $1 == m ":latest") { found = 1 } END { exit !found }'; then
        fail "El modelo '$OLLAMA_MODEL' no aparece en 'ollama list'. La instalacion esta arriba, pero el chat no puede responder."
    fi
fi
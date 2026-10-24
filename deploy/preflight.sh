#!/usr/bin/env sh
# Verifica que el host y el .env puedan levantar la instalacion. No modifica
# nada: es seguro correrlo antes de un setup, upgrade o restore.
set -eu
. ./lib.sh

command -v docker >/dev/null 2>&1 || fail "Docker no esta instalado o no esta en PATH."
docker compose version >/dev/null 2>&1 || fail "Falta el plugin 'docker compose' (v2)."
docker info >/dev/null 2>&1 || fail "El daemon de Docker no responde. Arranquelo y reintente."
# openssl solo lo necesita setup.sh (secretos y clave del admin). upgrade,
# backup y restore llaman a este preflight y no lo usan: exigirlo aca dejaba
# un host minimo sin poder actualizar.

[ -f .env ] || fail "No existe deploy/.env. Copie .env.example a .env y edite los valores, o corra ./setup.sh."

SECRET_KEY=$(env_get SECRET_KEY || true)
FERNET_KEY=$(env_get FERNET_KEY || true)
POSTGRES_USER=$(env_get POSTGRES_USER || true)
POSTGRES_PASSWORD=$(env_get POSTGRES_PASSWORD || true)
POSTGRES_DB=$(env_get POSTGRES_DB || true)
BACKEND_IMAGE=$(env_get DATIA_BACKEND_IMAGE || true)
FRONTEND_IMAGE=$(env_get DATIA_FRONTEND_IMAGE || true)
DATIA_BIND_IP=$(strip_quotes "$(env_get DATIA_BIND_IP || true)")
DATIA_HTTP_PORT=$(env_get DATIA_HTTP_PORT || true)
TRUSTED_PROXY_CIDR=$(env_get TRUSTED_PROXY_CIDR || true)
LLM_MODE=$(env_get LLM_MODE || true)
OLLAMA_BASE_URL=$(env_get OLLAMA_BASE_URL || true)
PROFILES=${COMPOSE_PROFILES:-$(env_get COMPOSE_PROFILES || true)}

[ -n "$SECRET_KEY" ] || fail "Falta SECRET_KEY en .env."
case "$SECRET_KEY" in CHANGE_ME*) fail "SECRET_KEY sigue con el placeholder. Corra ./setup.sh o edite .env." ;; esac
[ "${#SECRET_KEY}" -ge 32 ] || fail "SECRET_KEY es demasiado corta (minimo 32 caracteres)."

[ -n "$FERNET_KEY" ] || fail "Falta FERNET_KEY en .env."
case "$FERNET_KEY" in CHANGE_ME*) fail "FERNET_KEY sigue con el placeholder. Corra ./setup.sh o edite .env." ;; esac
# Fernet = 32 bytes en base64 url-safe: 43 caracteres del alfabeto y un '=' de
# padding. Aceptar '=' en cualquier posicion dejaba pasar una clave que fallaba
# recien al descifrar la primera credencial guardada.
printf '%s' "$FERNET_KEY" | grep -Eq '^[A-Za-z0-9_-]{43}=$' \
    || fail "FERNET_KEY no es una clave Fernet valida (43 caracteres base64 url-safe y un '=' final)."

[ -n "$POSTGRES_USER" ] || fail "Falta POSTGRES_USER en .env."
[ -n "$POSTGRES_PASSWORD" ] || fail "Falta POSTGRES_PASSWORD en .env."
case "$POSTGRES_PASSWORD" in CHANGE_ME*) fail "POSTGRES_PASSWORD sigue con el placeholder." ;; esac
[ "${#POSTGRES_PASSWORD}" -ge 12 ] || fail "POSTGRES_PASSWORD es demasiado corta (minimo 12 caracteres)."
[ -n "$POSTGRES_DB" ] || fail "Falta POSTGRES_DB en .env."

[ -n "$BACKEND_IMAGE" ] || fail "Falta DATIA_BACKEND_IMAGE en .env."
[ -n "$FRONTEND_IMAGE" ] || fail "Falta DATIA_FRONTEND_IMAGE en .env."
case "$BACKEND_IMAGE" in *:latest) warn "DATIA_BACKEND_IMAGE usa :latest; para produccion fije un tag o digest inmutable." ;; esac
case "$FRONTEND_IMAGE" in *:latest) warn "DATIA_FRONTEND_IMAGE usa :latest; para produccion fije un tag o digest inmutable." ;; esac

case "$LLM_MODE" in
    central|external|user) : ;;
    "") fail "Falta LLM_MODE en .env (central, external o user)." ;;
    *) fail "LLM_MODE invalido: '$LLM_MODE' (use central, external o user)." ;;
esac
[ -n "$OLLAMA_BASE_URL" ] || fail "Falta OLLAMA_BASE_URL en .env."

case "$DATIA_HTTP_PORT" in
    ''|*[!0-9]*) fail "DATIA_HTTP_PORT invalido: '$DATIA_HTTP_PORT'." ;;
esac
[ "$DATIA_HTTP_PORT" -ge 1 ] && [ "$DATIA_HTTP_PORT" -le 65535 ] || fail "DATIA_HTTP_PORT fuera de rango."

case "$TRUSTED_PROXY_CIDR" in
    ''|0.0.0.0/0|::/0) fail "TRUSTED_PROXY_CIDR no puede ser vacio ni 0.0.0.0/0 (confiaria en cualquier X-Forwarded-For)." ;;
esac

# Si el stack ya esta corriendo, el puerto ocupado es el nuestro: no es un
# conflicto. Si no, el puerto debe estar libre.
if ! docker compose ps --status running --services 2>/dev/null | grep -qx frontend; then
    if port_in_use "$DATIA_HTTP_PORT"; then
        fail "El puerto $DATIA_HTTP_PORT ya esta ocupado. Cambie DATIA_HTTP_PORT o detenga el otro servicio."
    fi
fi

# Perfil caddy: exige dominio y que 80/443 esten libres (salvo que el stack ya
# tenga caddy corriendo).
case ",$PROFILES," in
    *,caddy,*)
        DATIA_DOMAIN=$(env_get DATIA_DOMAIN || true)
        [ -n "$DATIA_DOMAIN" ] || fail "COMPOSE_PROFILES incluye caddy pero DATIA_DOMAIN esta vacio."
        for _p in 80 443; do
            if ! docker compose ps --status running --services 2>/dev/null | grep -qx caddy; then
                if port_in_use "$_p"; then
                    fail "El perfil caddy necesita el puerto $_p libre."
                fi
            fi
        done
        ;;
esac

case "$OLLAMA_BASE_URL" in
    http://localhost*|http://127.0.0.1*|http://\[::1\]*)
        warn "OLLAMA_BASE_URL apunta a loopback. Dentro del contenedor, localhost es el backend. Use http://ollama:11434 o http://host.docker.internal:11434."
        ;;
esac

if [ "$LLM_MODE" = "user" ]; then
    warn "LLM_MODE=user: los usuarios autenticados podran apuntar el motor a hosts de la red privada. Habilite solo en una red segmentada."
fi

if [ "$DATIA_BIND_IP" = "0.0.0.0" ]; then
    warn "DATIA_BIND_IP=0.0.0.0 publica el frontend en todas las interfaces. Asegurese de que solo lo alcance su LB/ingress."
fi

# Permisos del .env: contiene SECRET_KEY, FERNET_KEY y la clave de Postgres.
_perms=$(stat -c '%a' .env 2>/dev/null || stat -f '%Lp' .env 2>/dev/null || true)
if [ -n "$_perms" ] && [ "$_perms" -gt 600 ] 2>/dev/null; then
    warn ".env tiene permisos $_perms. Restrinjalos: chmod 600 .env"
fi

# Espacio y memoria: Ollama + imagenes + modelos necesitan varios GB. No se
# falla por esto para no bloquear una VM de prueba, pero el aviso es real.
_free_kb=$(df -Pk "$SCRIPT_DIR" 2>/dev/null | awk 'NR == 2 { print $4 }')
if [ -n "$_free_kb" ] && [ "$_free_kb" -lt 10485760 ] 2>/dev/null; then
    warn "Quedan menos de 10 GB libres. Las imagenes y un modelo 7B pueden no entrar."
fi
if [ -r /proc/meminfo ]; then
    _mem_kb=$(awk '/MemAvailable/ { print $2 }' /proc/meminfo)
    if [ -n "$_mem_kb" ] && [ "$_mem_kb" -lt 8388608 ] 2>/dev/null; then
        warn "Menos de 8 GB de RAM disponible: un modelo 7B puede no cargar. Use un modelo mas chico o revise OLLAMA_KEEP_ALIVE."
    fi
fi

# Compose interpola todo el archivo, incluido el perfil caddy. config -q detecta
# variables faltantes y errores de sintaxis antes de tocar contenedores.
docker compose config -q || fail "docker compose config fallo. Revise .env y docker-compose.yml."

printf 'Preflight OK: Docker, .env y compose consistentes.\n'

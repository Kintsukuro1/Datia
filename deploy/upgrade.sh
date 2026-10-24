#!/usr/bin/env sh
# Actualiza las imagenes del stack sin perder metadata ni data_sources.
#
#   ./upgrade.sh
#
# Edite DATIA_BACKEND_IMAGE y DATIA_FRONTEND_IMAGE en .env (tag o digest) antes
# de correrlo. Hace un backup previo y corrige el owner del volumen cuando la
# instalacion viene de una imagen vieja que corria como root.
set -eu
. ./lib.sh

DATIA_BACKEND_IMAGE=$(env_get DATIA_BACKEND_IMAGE || true)
DATIA_FRONTEND_IMAGE=$(env_get DATIA_FRONTEND_IMAGE || true)
[ -n "$DATIA_BACKEND_IMAGE" ] || fail "Falta DATIA_BACKEND_IMAGE en .env."
[ -n "$DATIA_FRONTEND_IMAGE" ] || fail "Falta DATIA_FRONTEND_IMAGE en .env."

./preflight.sh

printf 'Backup previo al upgrade...\n'
./backup.sh

printf 'Descargando imagenes nuevas...\n'
if [ "${DATIA_SKIP_PULL:-$(env_get DATIA_SKIP_PULL || true)}" = "1" ]; then
    warn "DATIA_SKIP_PULL=1: se usan las imagenes ya cargadas (instalacion air-gapped)."
else
    docker compose pull || fail "No se pudieron descargar las imagenes nuevas. Si los paquetes de GHCR son privados, revise 'docker login ghcr.io'."
fi

printf 'Deteniendo backend y frontend...\n'
docker compose stop backend frontend

# El volumen data_sources pudo crearse con una imagen anterior que corria como
# root. La imagen nueva usa UID 10001; si el owner no se corrige, el backend
# arranca pero no puede leer las bases subidas.
printf 'Corrigiendo owner de data_sources...\n'
docker compose run --rm --no-deps --user root --entrypoint sh backend \
    -c "chown -R 10001:10001 /app/data_sources"

printf 'Levantando la version nueva...\n'
docker compose up -d --wait --wait-timeout 600

printf '\nUpgrade completo.\n'
docker compose ps
printf '\nSi algo falla, vuelva a fijar el tag anterior en .env y corra:\n'
printf '  docker compose up -d --wait\n'
printf 'El esquema lo aplica el backend al arrancar. Si la version nueva migro\n'
printf 'datos de forma incompatible, restaure el backup con ./restore.sh.\n'

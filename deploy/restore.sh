#!/usr/bin/env sh
# Restaura un backup creado por backup.sh. REEMPLAZA la metadata y los datos
# subidos: exige confirmacion explicita.
#
#   ./restore.sh backups/20261023T120000Z.dump
#   ./restore.sh backups/20261023T120000Z.dump --yes
set -eu
. ./lib.sh

BACKUP_DIR=${BACKUP_DIR:-$SCRIPT_DIR/backups}
ARG=${1:-}
CONFIRM=${2:-}

if [ -z "$ARG" ]; then
    printf 'Backups disponibles:\n' >&2
    ls -1 "$BACKUP_DIR"/*.dump 2>/dev/null >&2 || true
    fail "Uso: ./restore.sh <archivo.dump | directorio-de-backup> [--yes]"
fi

if [ -f "$ARG" ]; then
    DUMP=$ARG
    BACKUP=$(dirname "$DUMP")
    stamp=$(basename "$DUMP" .dump)
elif [ -d "$ARG" ]; then
    BACKUP=$ARG
    DUMP=""
    for _f in "$BACKUP"/*.dump; do
        [ -f "$_f" ] && DUMP=$_f
    done
    [ -n "$DUMP" ] || fail "No hay ningun .dump en $BACKUP."
    stamp=$(basename "$DUMP" .dump)
else
    fail "No existe '$ARG'."
fi

DATA_TGZ="$BACKUP/$stamp-data_sources.tar.gz"
ENV_COPY="$BACKUP/$stamp-env"
[ -f "$DATA_TGZ" ] || fail "Falta $DATA_TGZ."
[ -f "$ENV_COPY" ] || fail "Falta $ENV_COPY: sin el no se puede verificar FERNET_KEY."

if [ "$CONFIRM" != "--yes" ] && [ "${RESTORE_YES:-0}" != "1" ]; then
    printf 'Esto REEMPLAZA la metadata y /app/data_sources de la instalacion actual.\n'
    printf "Escriba 'si' para continuar: "
    read -r answer
    [ "$answer" = "si" ] || fail "Restore cancelado."
fi

POSTGRES_USER=$(env_get POSTGRES_USER || true)
POSTGRES_DB=$(env_get POSTGRES_DB || true)
CURRENT_FERNET=$(env_get FERNET_KEY || true)
CURRENT_SECRET=$(env_get SECRET_KEY || true)
BACKUP_FERNET=$(env_get_file "$ENV_COPY" FERNET_KEY || true)
BACKUP_SECRET=$(env_get_file "$ENV_COPY" SECRET_KEY || true)

[ -n "$POSTGRES_USER" ] || fail "Falta POSTGRES_USER en .env."
[ -n "$POSTGRES_DB" ] || fail "Falta POSTGRES_DB en .env."

# Sin la misma FERNET_KEY, las credenciales cifradas de los conectores del
# backup quedan ilegibles. Es un fallo de efecto diferido: la app arranca y solo
# explota en la primera consulta que descifra una credencial.
if [ -n "$BACKUP_FERNET" ] || [ -n "$CURRENT_FERNET" ]; then
    [ "$BACKUP_FERNET" = "$CURRENT_FERNET" ] || fail "FERNET_KEY del backup no coincide con .env. Restaure el .env del backup o recifre las credenciales; si no, los conectores no podran descifrar sus claves."
elif [ "$BACKUP_SECRET" != "$CURRENT_SECRET" ]; then
    fail "Sin FERNET_KEY explicita, la clave se deriva de SECRET_KEY y esta no coincide con el backup."
fi
if [ -n "$BACKUP_SECRET" ] && [ "$BACKUP_SECRET" != "$CURRENT_SECRET" ]; then
    warn "SECRET_KEY distinta: las sesiones existentes quedaran invalidas (no afecta datos ni credenciales cifradas)."
fi

# Validar integridad ANTES de tocar la metadata. Un dump corrupto o un tar
# truncado no deben dejar la instalacion sin base y sin forma de volver.
docker compose ps --status running --services 2>/dev/null | grep -qx postgres \
    || fail "PostgreSQL no esta corriendo. Levante el stack (sin restaurar) antes de correr restore.sh."
if [ -f "$BACKUP/$stamp.sha256" ]; then
    if command -v sha256sum >/dev/null 2>&1; then
        (cd "$BACKUP" && sha256sum -c "$stamp.sha256") || fail "Checksum del backup no coincide. No se restauro nada."
    else
        (cd "$BACKUP" && shasum -a 256 -c "$stamp.sha256") || fail "Checksum del backup no coincide. No se restauro nada."
    fi
fi
docker compose exec -T postgres pg_restore --list < "$DUMP" >/dev/null \
    || fail "El dump no es un backup valido de pg_restore. No se restauro nada."
tar -tzf "$DATA_TGZ" >/dev/null \
    || fail "El tar de data_sources esta corrupto. No se restauro nada."

printf 'Deteniendo backend y frontend...\n'
docker compose stop backend frontend

printf 'Restaurando metadata...\n'
_db_escaped=$(printf '%s' "$POSTGRES_DB" | sed "s/'/''/g")
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 \
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$_db_escaped' AND pid <> pg_backend_pid();" >/dev/null
docker compose exec -T postgres dropdb -U "$POSTGRES_USER" --if-exists "$POSTGRES_DB"
docker compose exec -T postgres createdb -U "$POSTGRES_USER" "$POSTGRES_DB"
docker compose exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    --no-owner --clean --if-exists < "$DUMP"

printf 'Restaurando data_sources...\n'
_tmp=$(mktemp -d)
trap 'rm -rf "$_tmp"' EXIT
tar -xzf "$DATA_TGZ" -C "$_tmp"
# Vacia el volumen antes de copiar: `docker cp` solo sobrescribe, asi que un
# archivo subido despues del backup sobreviviria y el restore no seria el
# estado del backup. `find -delete` no falla con el volumen vacio.
docker compose run --rm --no-deps --user root --entrypoint sh backend \
    -c "find /app/data_sources -mindepth 1 -delete"
docker compose cp "$_tmp/." backend:/app/data_sources/ >/dev/null

# docker cp escribe como root; el backend corre como UID 10001. Sin este chown,
# la app arranca pero no puede leer ni escribir las bases subidas.
docker compose run --rm --no-deps --user root --entrypoint sh backend \
    -c "chown -R 10001:10001 /app/data_sources"

printf 'Levantando servicios...\n'
docker compose up -d --wait --wait-timeout 300

printf 'Restore completo desde %s\n' "$stamp"
docker compose ps

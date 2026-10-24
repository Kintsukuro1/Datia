#!/usr/bin/env sh
# Backup consistente de la metadata, los datos subidos y el .env.
#
# El .env se incluye a proposito: sin FERNET_KEY las credenciales cifradas de
# los conectores no se pueden descifrar al restaurar. Trate el backup como un
# secreto (contiene claves) y guardelo cifrado fuera del host.
set -eu
. ./lib.sh

# El backup contiene el .env (con FERNET_KEY) y el dump de la metadata: el
# umask del host no debe dejarlos legibles para otros usuarios.
umask 077

BACKUP_DIR=${BACKUP_DIR:-$SCRIPT_DIR/backups}
mkdir -p "$BACKUP_DIR"

POSTGRES_USER=$(env_get POSTGRES_USER || true)
POSTGRES_DB=$(env_get POSTGRES_DB || true)
[ -n "$POSTGRES_USER" ] || fail "Falta POSTGRES_USER en .env."
[ -n "$POSTGRES_DB" ] || fail "Falta POSTGRES_DB en .env."

docker compose ps --status running --services 2>/dev/null | grep -qx backend \
    || fail "El backend no esta corriendo. Levante el stack con ./setup.sh o docker compose up -d antes de respaldar."

stamp=$(date -u +%Y%m%dT%H%M%SZ)
dump="$BACKUP_DIR/$stamp.dump"
data_tgz="$BACKUP_DIR/$stamp-data_sources.tar.gz"
env_copy="$BACKUP_DIR/$stamp-env"
images_txt="$BACKUP_DIR/$stamp-images.txt"
work="$BACKUP_DIR/$stamp-data_sources"

# Las imagenes que estan corriendo AHORA son el destino real de un rollback.
# El .env que se respalda ya puede traer los tags nuevos si el operador los
# edito antes del upgrade; este archivo no.
printf 'Registrando imagenes en ejecucion...\n'
docker compose images > "$images_txt" 2>/dev/null || true

printf 'Respaldando metadata...\n'
if ! docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    --format=custom --no-owner > "$dump"; then
    rm -f "$dump"
    fail "pg_dump fallo; no se genero un backup valido."
fi

printf 'Respaldando data_sources...\n'
mkdir -p "$work"
docker compose cp backend:/app/data_sources/. "$work/" >/dev/null
tar -czf "$data_tgz" -C "$work" .
rm -rf "$work"

# Copia del .env con permisos restringidos.
cp .env "$env_copy"
chmod 600 "$env_copy" 2>/dev/null || true

if command -v sha256sum >/dev/null 2>&1; then
    (cd "$BACKUP_DIR" && sha256sum "$(basename "$dump")" "$(basename "$data_tgz")" "$(basename "$env_copy")" "$(basename "$images_txt")" > "$stamp.sha256")
else
    (cd "$BACKUP_DIR" && shasum -a 256 "$(basename "$dump")" "$(basename "$data_tgz")" "$(basename "$env_copy")" "$(basename "$images_txt")" > "$stamp.sha256")
fi

printf '\nBackup listo: %s\n' "$BACKUP_DIR"
printf '  %s\n' "$dump"
printf '  %s\n' "$data_tgz"
printf '  %s (contiene secretos)\n' "$env_copy"
printf '  %s (imagenes en ejecucion, para rollback)\n' "$images_txt"
printf '  %s\n' "$BACKUP_DIR/$stamp.sha256"
printf '\nRestaure con: ./restore.sh %s\n' "$BACKUP_DIR/$stamp.dump"

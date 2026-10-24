#!/usr/bin/env sh
# Helpers comunes de los scripts del bundle. No ejecuta nada por si solo.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

warn() {
    printf 'AVISO: %s\n' "$*" >&2
}

# Lee una clave de un archivo .env sin sourcearlo: sourcear rompe con valores
# como CORS_ORIGINS=["http://..."]. Devuelve la ultima aparicion.
env_get_file() {
    _file=$1
    _key=$2
    [ -f "$_file" ] || return 1
    awk -v k="$_key" '
        index($0, k "=") == 1 { v = substr($0, length(k) + 2) }
        END { if (v != "") print v }
    ' "$_file"
}

env_get() {
    env_get_file .env "$1"
}

# Imprime el valor sin comillas simples/dobles envolventes.
strip_quotes() {
    case "$1" in
        \"*\") _v=${1#\"}; _v=${_v%\"} ;;
        \'*\') _v=${1#\'}; _v=${_v%\'} ;;
        *) _v=$1 ;;
    esac
    printf '%s' "$_v"
}

# Escribe KEY=value en .env reemplazando la linea existente o agregandola.
# Usa awk para no pelear con el escaping de sed: los secretos traen +, / y =.
replace_env() {
    _key=$1
    _value=$2
    _tmp=$(mktemp)
    awk -v k="$_key" -v v="$_value" '
        index($0, k "=") == 1 { print k "=" v; done = 1; next }
        { print }
        END { if (!done) print k "=" v }
    ' .env > "$_tmp"
    mv "$_tmp" .env
}

port_in_use() {
    _port=$1
    if command -v ss >/dev/null 2>&1; then
        ss -ltn 2>/dev/null | awk -v p=":$_port$" 'NR > 1 && $4 ~ p { found = 1 } END { exit !found }'
    elif command -v netstat >/dev/null 2>&1; then
        netstat -ltn 2>/dev/null | awk -v p=":$_port$" '$4 ~ p { found = 1 } END { exit !found }'
    else
        return 1
    fi
}

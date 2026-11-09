#!/usr/bin/env sh
# Recolecta un paquete de diagnóstico para soporte, sin datos de clientes.
# Uso: ./support-bundle.sh
set -eu

cd "$(dirname "$0")"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out_dir="support"
bundle="$out_dir/datia-support-$stamp"
mkdir -p "$bundle"

compose() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  else
    docker-compose "$@"
  fi
}

{
  printf 'version='; cat VERSION 2>/dev/null || printf 'desconocida\n'
  printf 'date='; date -u +%Y-%m-%dT%H:%M:%SZ
  printf 'host='; hostname
  printf 'uname='; uname -a
} > "$bundle/entorno.txt"

compose ps > "$bundle/compose-ps.txt" 2>&1 || true
compose images > "$bundle/compose-images.txt" 2>&1 || true
docker version > "$bundle/docker-version.txt" 2>&1 || true
df -h > "$bundle/disk.txt" 2>&1 || true
free -m > "$bundle/memory.txt" 2>&1 || true

# Solo nombres de variables: un .env incluye SECRET_KEY, FERNET_KEY y claves
# de conectores. Nunca se copia el archivo ni sus valores.
if [ -f .env ]; then
  grep -E '^[A-Za-z_][A-Za-z0-9_]*=' .env | cut -d= -f1 | sort > "$bundle/env-keys.txt" || true
fi

compose logs --no-color --tail=500 backend > "$bundle/backend.log" 2>&1 || true
compose logs --no-color --tail=200 frontend > "$bundle/frontend.log" 2>&1 || true
compose logs --no-color --tail=200 ollama > "$bundle/ollama.log" 2>&1 || true

# El backend no publica puerto: se consulta desde dentro de su contenedor.
compose exec -T backend curl -fsS http://127.0.0.1:8000/version > "$bundle/version.json" 2>&1 || true
compose exec -T backend curl -fsS http://127.0.0.1:8000/health/ready > "$bundle/health-ready.json" 2>&1 || true
compose exec -T backend curl -fsS http://127.0.0.1:8000/metrics > "$bundle/metrics.txt" 2>&1 || true

tar -czf "$bundle.tar.gz" -C "$out_dir" "$(basename "$bundle")"
rm -rf "$bundle"
printf 'Paquete de soporte: %s\n' "$bundle.tar.gz"
printf 'Reviselo antes de enviarlo: los logs pueden contener preguntas de usuarios.\n'

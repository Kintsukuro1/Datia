# Datia - instalacion on-prem

Bundle de instalacion para **una empresa por instalacion** (single-tenant). El
cliente recibe este directorio y las imagenes de GHCR; no recibe el repositorio
de codigo.

## Que levanta

```
navegador
   |
   | HTTP/HTTPS (LB/ingress de la empresa o Caddy opcional)
   v
frontend (nginx sin privilegios, :8080)
   |
   | /api/ -> backend:8000 (red interna, sin puerto publicado)
   v
backend (FastAPI, UID 10001)
   |-------------------> PostgreSQL 16 (metadata, sin puerto publicado)
   |-------------------> Ollama (LLM central, sin puerto publicado)
   |
   +-------------------> bases corporativas del cliente (conectores de solo lectura)
```

- **PostgreSQL** guarda la metadata, RBAC, catalogo y auditoria.
- **data_sources** (volumen) guarda las bases y archivos que sube el cliente.
- El **backend** es el unico que ejecuta SQL, valida permisos, enmascara
  columnas y escribe `audit_logs`.
- **Ollama** es el motor por defecto. La empresa puede usar un endpoint propio
  o habilitar el LLM por usuario (ver mas abajo).
- El backend y PostgreSQL **no publican puertos**. Solo el frontend, y por
  defecto en loopback.

## Requisitos

| Recurso | Minimo | Recomendado |
|---|---|---|
| CPU | 4 vCPU | 8 vCPU |
| RAM | 8 GB | 16 GB (modelo 7B en CPU) |
| Disco | 20 GB libres | 50 GB+ (modelos e imagenes) |
| SO | Linux x86_64/arm64 con Docker | Ubuntu 22.04/24.04 LTS |
| Docker | Engine 24+ y Compose 2.20+ | ultima estable |
| Utilidades | `openssl` (solo `setup.sh`) | — |

**Memoria para el modelo.** El compose no impone un limite a Ollama: el
contenedor reserva lo que necesita el modelo elegido. Los 8/16 GB de la tabla
alcanzan para un 7B cuantizado (Q4, ~5 GB en uso). Para 14B prevea 16 GB libres
y para 32B, 32 GB, mas 2-4 GB para PostgreSQL, backend y sistema. Si el host es
compartido, limite el servicio en un override de compose con `mem_limit`; un
limite menor al que necesita el modelo lo hace fallar al cargarlo.

Salida a Internet para bajar imagenes y modelos. Para una instalacion
air-gapped, vea [Air-gapped](#air-gapped).

Si el host ya usa las subredes `172.30.0.0/24` o `172.30.1.0/24`, cambie las
subredes en `docker-compose.yml` y ajuste `TRUSTED_PROXY_CIDR`.

## Instalacion

1. Descomprima el bundle:

   ```sh
   tar xzf datia-deploy-vX.Y.Z.tar.gz
   cd datia-deploy-vX.Y.Z
   ```

2. Los paquetes de GHCR son privados. Autentiquese con un token que tenga
   `read:packages`:

   ```sh
   docker login ghcr.io -u <usuario>
   ```

   Si el responsable los hizo publicos, este paso no hace falta.

3. Revise `.env.example` y ajuste lo que necesite antes de instalar. Lo mas
   comun:

   ```sh
   # .env se crea en el paso siguiente; estos son los valores que querra editar
   DATIA_BIND_IP=127.0.0.1     # 0.0.0.0 solo si un LB de confianza lo alcanza
   LLM_MODE=central            # central | external | user
   OLLAMA_MODEL=qwen2.5-coder:7b
   #DATIA_DOMAIN=datia.empresa.com
   ```

4. Ejecute el instalador:

   ```sh
   ./setup.sh
   # si el bit de ejecucion no viajo: sh setup.sh
   ```

   `setup.sh`:

   - copia `.env.example` a `.env` con permisos 600;
   - genera `SECRET_KEY`, `FERNET_KEY` y `POSTGRES_PASSWORD` aleatorios;
   - fija las imagenes al tag del bundle (`VERSION`);
   - valida el host con `preflight.sh`;
   - baja las imagenes y levanta el stack (`DATIA_SKIP_PULL=1` lo omite en
     instalaciones air-gapped);
   - descarga `OLLAMA_MODEL`;
   - crea el primer admin (sin cuentas demo) y muestra su clave inicial **una
     sola vez**. La cuenta debe cambiarla en el primer login.

5. Abra la URL que imprime el instalador (`http://127.0.0.1:8080` por defecto),
   entre con el admin y cambie la clave.

Para reinstalar desde cero en una maquina de prueba:

```sh
docker compose down -v   # BORRA metadata, data_sources y modelos
rm .env
./setup.sh
```

## TLS

### Opcion A: LB/ingress de la empresa (recomendado si ya existe)

El frontend escucha HTTP en `DATIA_BIND_IP:DATIA_HTTP_PORT` (8080 por defecto).
Configure el LB para terminar TLS y proxear a esa direccion. Si el LB no corre
en el host, publique el frontend en una interfaz que el LB alcance:

```sh
DATIA_BIND_IP=0.0.0.0
```

y restrinja el puerto 8080 en el firewall a la IP del LB. Ajuste
`TRUSTED_PROXY_CIDR` al CIDR desde el que llega el LB (el default
`172.30.0.0/24` solo cubre Caddy y la red `edge` del compose). El LB debe
mandar `X-Forwarded-For`; nginx solo lo cree si viene de ese CIDR.

### Opcion B: Caddy en la misma VM

Si el dominio resuelve a esta VM y los puertos 80/443 estan libres:

```sh
# en .env
DATIA_DOMAIN=datia.empresa.com
COMPOSE_PROFILES=caddy
```

y vuelva a levantar:

```sh
./preflight.sh
docker compose up -d --wait
```

Caddy gestiona el certificado ACME. Si prefiere no poner `COMPOSE_PROFILES` en
`.env`, use `docker compose --profile caddy up -d --wait` en cada arranque.

## Motor LLM

La politica se elige con `LLM_MODE` (variable de empresa, no del navegador). El
frontend consulta `GET /api/v1/system/llm-policy` y deshabilita los campos de
Ajustes cuando la empresa administra el motor.

| `LLM_MODE` | Quien elige URL/modelo | Uso |
|---|---|---|
| `central` (default en produccion) | La empresa; el servicio `ollama` del compose | Instalacion estandar. El navegador no puede cambiar el endpoint. |
| `external` | La empresa; un endpoint fuera del compose | Ollama en otro contenedor del host, otra maquina de la LAN o un gateway compatible. |
| `user` | Cada usuario, dentro de la red privada | La empresa habilita el LLM propio por usuario. La URL se valida en el backend. |

En `central` y `external`, la URL y el modelo que mande el cliente **se
ignoran**: el backend usa `OLLAMA_BASE_URL`/`OLLAMA_MODEL` (o
`OPENAI_COMPATIBLE_URL` si `LLM_PROVIDER=openai_compatible`). Una URL hostil no
genera request ni 400; simplemente no se usa. El nombre del modelo se toma
siempre de `OLLAMA_MODEL`, tambien con `openai_compatible`: escriba ahi el id
que espera ese servidor.

En `user`, el backend acepta loopback, `host.docker.internal` y rangos RFC1918
(`10/8`, `172.16/12`, `192.168/16`). Rechaza IPs publicas, link-local
(`169.254.0.0/16`, incluido el endpoint de metadatos cloud) y hostnames internos
que no sean `host.docker.internal`. El backend sigue ejecutando el SQL y
auditando: el usuario solo elige a que motor se manda el prompt.

La URL la resuelve el **backend**, no el navegador: `127.0.0.1` apunta al
contenedor del backend, no a la PC del usuario. Para el LLM de una estacion, use
la IP de esa estacion en la LAN (por ejemplo `http://192.168.1.50:11434`).

En `user` **no hay fallback**: si el motor del usuario falla, la consulta falla
con el motivo. El backend no reintenta contra el motor de la empresa, porque eso
mandaria el prompt a un servidor que el usuario no eligio.

> **Riesgo de `user`:** un usuario autenticado puede mandar el prompt (y parte
> del esquema) a cualquier host y puerto de la red privada, y usar los errores
> del backend para saber si ese puerto responde. Los cuerpos de respuesta del
> destino no se devuelven. Habilítelo solo si esa red esta segmentada y los
> usuarios son de confianza.

### Ollama externo al compose

Si la empresa ya tiene un Ollama en otro contenedor del host:

```sh
# .env
LLM_MODE=external
OLLAMA_BASE_URL=http://host.docker.internal:11434
```

El backend ya trae `host.docker.internal:host-gateway`. Si el Ollama esta en
otra maquina, use su IP privada y asegure el firewall. El servicio `ollama` del
compose sigue corriendo vacio; si prefiere no gastar recursos, quite el servicio
y su entrada en `backend.depends_on`.

### Modelo y GPU

`setup.sh` descarga `OLLAMA_MODEL` solo si `OLLAMA_BASE_URL` apunta al servicio
local `ollama`. En `external` no descarga nada. Para cambiarlo en la instalacion
central:

```sh
docker compose exec ollama ollama pull <modelo>
# edite OLLAMA_MODEL en .env y recree el backend
docker compose up -d backend
```

La ventana de contexto del LLM se fija al arrancar el servidor, no por request.
En Ollama, ajuste el modelo/Modelfile; `LLM_NUM_CTX` solo aplica a servidores que
lo aceptan por request (por ejemplo llama.cpp). Para GPU NVIDIA:

```sh
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --wait
```

Requiere driver NVIDIA y NVIDIA Container Toolkit. Sin GPU, Ollama corre en CPU.

## Operacion

### Estado y logs

```sh
docker compose ps
docker compose logs -f backend
docker compose logs -f frontend
docker compose logs -f ollama
```

`/health/live` y `/health/ready` son del backend; nginx expone `/healthz` para su
propio healthcheck. El backend no publica esos puertos al host.

### Backup

```sh
./backup.sh
```

Crea en `backups/`:

- `<stamp>.dump`: metadata PostgreSQL (formato custom);
- `<stamp>-data_sources.tar.gz`: bases y archivos subidos;
- `<stamp>-env`: copia de `.env` (**contiene secretos**);
- `<stamp>-images.txt`: imagenes que estaban corriendo (tag de rollback);
- `<stamp>.sha256`.

El `.env` se incluye porque sin `FERNET_KEY` las credenciales cifradas de los
conectores no se pueden descifrar al restaurar. Guarde el backup cifrado y fuera
del host. Sin la misma `FERNET_KEY`, el restore se niega a continuar.

El backup no detiene el stack: `pg_dump` es una foto consistente, pero el tar de
`data_sources` copia archivos en vivo. Corralo sin subidas en curso; si necesita
consistencia estricta, detenga backend/frontend antes
(`docker compose stop backend frontend`) y levantelos al terminar.

### Restore

```sh
./restore.sh backups/20261023T120000Z.dump
# o sin confirmacion interactiva:
./restore.sh backups/20261023T120000Z.dump --yes
```

Detiene backend/frontend, reemplaza la metadata y `data_sources`, corrige el
owner del volumen y vuelve a levantar. No sobrescribe `.env`; valida que
`FERNET_KEY` coincida.

### Upgrade

1. Edite `DATIA_BACKEND_IMAGE` y `DATIA_FRONTEND_IMAGE` en `.env` al tag/digest
   nuevo.
2. Ejecute:

   ```sh
   ./upgrade.sh
   ```

Hace backup, pull (salvo `DATIA_SKIP_PULL=1`), corrige el owner de
`data_sources` (instalaciones viejas corrian como root) y levanta con
`--wait`. El esquema lo aplica el backend al arrancar; no hay migraciones
manuales.

Rollback: vuelva a fijar el tag anterior y `docker compose up -d --wait`. El
backup previo deja en `backups/<stamp>-images.txt` las imagenes que estaban
corriendo: ese es el tag al que hay que volver. Si la version nueva migro datos
de forma incompatible, restaure el backup.

### Air-gapped

En un host sin Internet:

1. En una maquina con Internet, descargue las imagenes (si los paquetes de
   GHCR son privados, haga `docker login ghcr.io` tambien en esta maquina):

   ```sh
   docker pull ghcr.io/kintsukuro1/datia-backend:<tag>
   docker pull ghcr.io/kintsukuro1/datia-frontend:<tag>
   docker pull postgres:16.15-alpine
   docker pull ollama/ollama:0.40.10
   docker pull caddy:2.11.7-alpine   # solo si usa Caddy
   docker save ... | gzip > datia-images.tar.gz
   ```

2. En el host aislado: `docker load < datia-images.tar.gz`.
3. Copie el modelo de Ollama (volumen `ollama_models`) desde una maquina con
   Internet, o importe un Modelfile. Deje `DATIA_SKIP_MODEL_PULL=1` en `.env`.
4. Deje `DATIA_SKIP_PULL=1` en `.env` (o exportelo antes de correr el
   instalador). `setup.sh` y `upgrade.sh` no tocan el registry y usan las
   imagenes locales. Los tags tienen que coincidir con
   `DATIA_BACKEND_IMAGE`/`DATIA_FRONTEND_IMAGE`; si no, use `docker tag` para
   alinearlos.

## Seguridad incluida

- Backend, PostgreSQL y Ollama sin puertos publicados.
- Backend y nginx corren sin root (UID 10001 / nginx-unprivileged).
- `DEMO_MODE=false`: sin cuentas demo ni claves publicadas.
- Primer admin por CLI con `must_change_password=True`.
- `/docs` y OpenAPI apagados en produccion (`ENABLE_API_DOCS=false`).
- Rate limiting en login/cambio de password y en `/api/`.
- CSP con `connect-src 'self'`: el navegador no habla directo con el LLM.
- `SECRET_KEY`/`FERNET_KEY`/`POSTGRES_PASSWORD` obligatorios por compose; el
  backend rechaza la `SECRET_KEY` por defecto en produccion.
- Auditoria en `audit_logs` para consultas, permisos y acciones administrativas.

Lo que **no** cubre el bundle: hardening del host, firewall, TLS del LB,
segmentacion de red, backups fuera del host y gestion de parches del sistema
operativo. Son responsabilidad de la empresa.

## Troubleshooting

| Sintoma | Causa probable | Que hacer |
|---|---|---|
| `docker compose` falla por variable requerida | Falta `.env` o un secreto | Corra `./setup.sh` o complete `.env`; luego `./preflight.sh`. |
| `unauthorized` al bajar imagenes | Paquetes GHCR privados | `docker login ghcr.io` con un token `read:packages` que provee el responsable. |
| Frontend 502 | Backend no esta ready | `docker compose ps`; `docker compose logs backend`; espere `/health/ready`. |
| Chat: "IA local no disponible" | Modelo no descargado o `OLLAMA_BASE_URL` mal | `docker compose exec ollama ollama list`; descargue el modelo; en contenedor no use `localhost`. |
| `permission denied` en `data_sources` | Volumen viejo con owner root | `docker compose run --rm --no-deps --user root --entrypoint sh backend -c "chown -R 10001:10001 /app/data_sources"` |
| Restore se niega por `FERNET_KEY` | El `.env` actual no es el del backup | Restaure `<stamp>-env` o recifre las credenciales; no fuerce el restore. |
| Puerto 8080 ocupado | Otro servicio en el host | Cambie `DATIA_HTTP_PORT` en `.env` o detenga el otro servicio. |
| Caddy no obtiene certificado | DNS no apunta a la VM o 80/443 cerrados | Revise DNS, firewall y `docker compose logs caddy`. |

## Contenido del bundle

```
docker-compose.yml          stack de produccion
docker-compose.gpu.yml      override NVIDIA
Caddyfile                   TLS opcional
.env.example                plantilla de configuracion
setup.sh                    instalacion inicial
preflight.sh                validacion previa
backup.sh / restore.sh      respaldo y restauracion
upgrade.sh                  actualizacion de imagenes
lib.sh                      helpers comunes
README.md                   este documento
VERSION                     tag del release (lo agrega el release)
```

## Para quien publica el release

El workflow `.github/workflows/release.yml` corre con un tag `v*` y publica
`ghcr.io/kintsukuro1/datia-backend` y `ghcr.io/kintsukuro1/datia-frontend` con
el tag del release, mas este bundle adjunto. No requiere pasos manuales de
version.

Taguee solo un commit de `main` que ya paso CI: el workflow del release no
vuelve a correr la suite. Un tag `vX.Y.Z-rc.N` publica el GitHub Release como
prerelease y no mueve el tag `latest` de las imagenes.

Un paquete de GHCR hereda la visibilidad del repositorio cuando se publica la
primera vez, asi que con el repo publico las imagenes quedan publicas. Para
dejarlas privadas (lo esperado: el cliente recibe imagenes, no el codigo), una
sola vez despues del primer release:

```sh
gh api --method PATCH /user/packages/container/datia-backend -f visibility=private
gh api --method PATCH /user/packages/container/datia-frontend -f visibility=private
```

Hace falta un token con `write:packages` y ser admin del paquete. Los releases
siguientes conservan la visibilidad: no hay que repetirlo.

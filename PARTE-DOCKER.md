# Dat.ia — probado con Docker (sin instalar nada)

Solo necesitás **Docker Desktop**. No hace falta Node, ni Python, ni npm.

La primera vez baja ~310 MB. Después arranca en segundos.

Las tres imágenes viven en un solo repositorio: **`k1ntsu/dat.ia`**

---

## 1. Arrancar

### Opción rápida (recomendada)

Doble clic en **`setup.bat`**, en esta misma carpeta. El script hace todo:

- crea el `.env` a partir de `.env.example` y genera los tres valores que no
  pueden quedar en default: `FERNET_KEY`, `SECRET_KEY` y `POSTGRES_PASSWORD`;
- verifica que Docker esté instalado y encendido (si no, te lo dice y no hace
  nada más);
- baja las imágenes, arranca los tres servicios y abre **http://127.0.0.1**.

Se puede volver a ejecutar sin miedo: si ya había un `.env`, respeta los
valores configurados y solo completa lo que falte.

### Opción manual

En esta carpeta:

```bash
cp .env.example .env        # en Windows: copy .env.example .env
```

Editá `.env` y cambiá **tres** líneas:

```bash
# 1. Generá una clave y pegala. Comando:
#    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#    (si no tenés Python, abrilo online: https://cryptodoggy.com/tools/fernet-key-generator)
FERNET_KEY=pega_aqui_la_clave

# 2. Poné una contraseña para la base de datos
POSTGRES_PASSWORD=una_contrasena_cualquiera

# 3. Reemplazá la SECRET_KEY de la plantilla por cualquier cadena larga y
#    única. Si queda la del repositorio, el backend NO arranca: con
#    ENVIRONMENT=production la rechaza y el contenedor reinicia en bucle.
SECRET_KEY=otra_cadena_larga_y_unica
```

Después, **un solo comando**:

```bash
docker compose -f docker-compose.partner.yml up -d
```

Y abrí **http://127.0.0.1**

> Fijate que el comando lleva **solo** `docker-compose.partner.yml`. Si le
> agregás `-f docker-compose.yml`, ese archivo manda `build:` y te compila todo
> local en vez de usar las imágenes de Docker Hub.

---

## 2. Entrar

| Usuario | Contraseña |
| :--- | :--- |
| `admin` | `admin123` |
| `director` | `director123` |
| `economista` | `economista123` |
| `bi` | `bi123` |

Cada rol ve datos distintos. `admin` ve todo.

---

## 3. La IA (Ollama)

La app busca Ollama **en tu máquina**, no dentro de Docker, por eso funciona
sin configuración extra.

```bash
ollama serve
ollama pull qwen2.5-coder:7b
```

Mientras Ollama no esté corriendo, la app **no se rompe**: el chat responde
"IA local no disponible" y todo lo demás (roles, conectores, reportes) sigue
funcionando.

---

## 4. Problemas frecuentes

**En Mac con chip Apple Silicon (M1/M2/M3/M4) va lento**
Las imágenes se publicaron desde una máquina Intel, así que son solo `linux/amd64`.
Docker las emula y funciona, pero Postgres puede tardar varios minutos en
arrancar la primera vez. Es normal, no es un error. En un Windows o Linux x86-64
arranca al instante.

**Postgres dice "Database is uninitialized and superuser password is not specified"**
Te falta el `.env` o le falta `POSTGRES_PASSWORD`. El compose se niega a
arrancar antes de producir ese error, así que si lo estás viendo es que estás
usando el `docker-compose.yml` (el de desarrollo) en vez del
`docker-compose.partner.yml`. Revisá que el comando sea:
```bash
docker compose -f docker-compose.partner.yml up -d
```

**El puerto 80 está ocupado** (a veces lo agarra otro programa en Windows):
```bash
docker compose -f docker-compose.partner.yml down
# cambiá "127.0.0.1:80:8080" por "127.0.0.1:8080:8080" en docker-compose.partner.yml (sección frontend)
# y abrí http://127.0.0.1:8080
```

**Empezar de cero** (borra la base y toda la data):
```bash
docker compose -f docker-compose.partner.yml down -v
```

**El backend reinicia en bucle y los logs dicen "SECRET_KEY no fue configurado para producción"**
Quedó la `SECRET_KEY` de la plantilla en el `.env`. Ejecutá `setup.bat`
(la reemplaza sola) o cambiá esa línea a mano por cualquier cadena larga y
única. Con `ENVIRONMENT=production` el backend rechaza la clave de desarrollo.

**Windows bloquea `setup.bat` ("Windows protegió su PC" o "Advertencia de seguridad: editor desconocido")**
El archivo vino de internet (zip descargado) y Windows lo marca. Si el cartel
ofrece **Más información**, clic ahí y después en **Ejecutar de todas formas**.
Si en cambio es la advertencia con casilla, marcá **Ejecutar** igual. Para que
no vuelva a aparecer: clic derecho en `setup.bat` → **Propiedades** → abajo,
tildar **Desbloquear** → Aceptar (lo mismo para `setup.ps1`). O en PowerShell,
dentro de la carpeta:

```powershell
Unblock-File .\setup.bat, .\setup.ps1
```

**Ver los errores**:
```bash
docker compose -f docker-compose.partner.yml logs -f backend
```

**Parar**:
```bash
docker compose -f docker-compose.partner.yml down
```

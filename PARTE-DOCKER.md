# Dat.ia — probado con Docker (sin instalar nada)

Solo necesitás **Docker Desktop**. No hace falta Node, ni Python, ni npm.

La primera vez baja ~310 MB. Después arranca en segundos.

Las tres imágenes viven en un solo repositorio: **`k1ntsu/dat.ia`**

---

## 1. Arrancar

En esta carpeta:

```bash
cp .env.example .env        # en Windows: copy .env.example .env
```

Editá `.env` y cambiá **dos** líneas. Son las únicas dos que importan:

```bash
# 1. Generá una clave y pegala. Comando:
#    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#    (si no tenés Python, abrilo online: https://cryptodoggy.com/tools/fernet-key-generator)
FERNET_KEY=pega_aqui_la_clave

# 2. Poné una contraseña para la base de datos
POSTGRES_PASSWORD=una_contrasena_cualquiera
```

Después:

```bash
docker compose -f docker-compose.yml -f docker-compose.partner.yml up -d
```

Y abrí **http://localhost**

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

**"docker compose" no encuentra los archivos**
Usá los dos `-f`, o el orden importa:
```bash
docker compose -f docker-compose.yml -f docker-compose.partner.yml up -d
```

**El puerto 80 está ocupado** (a veces lo agarra otro programa en Windows):
```bash
docker compose -f docker-compose.yml -f docker-compose.partner.yml down
# cambiá "80:80" por "8080:80" en docker-compose.yml (línea 79) y abrí http://localhost:8080
```

**Empezar de cero** (borra la base y toda la data):
```bash
docker compose -f docker-compose.yml -f docker-compose.partner.yml down -v
```

**Ver los errores**:
```bash
docker compose -f docker-compose.yml -f docker-compose.partner.yml logs -f backend
```

**Parar**:
```bash
docker compose -f docker-compose.yml -f docker-compose.partner.yml down
```

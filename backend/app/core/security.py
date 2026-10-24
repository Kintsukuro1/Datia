import base64
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Union, Optional
import bcrypt
from cryptography.fernet import Fernet
from jose import jwt
from app.core.config import settings

logger = logging.getLogger(__name__)

def _get_fernet_key() -> bytes:
    """Generates a valid 32-byte url-safe base64 Fernet key derived from SECRET_KEY."""
    if settings.FERNET_KEY:
        return settings.FERNET_KEY.encode()
    digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return base64.urlsafe_b64encode(digest)

fernet = Fernet(_get_fernet_key())

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies plain password against hashed password (bcrypt)."""
    if not plain_password or not hashed_password:
        return False
    try:
        pwd_bytes = plain_password.encode('utf-8')
        hash_bytes = hashed_password.encode('utf-8')
        return bcrypt.checkpw(pwd_bytes, hash_bytes)
    except Exception as e:
        logger.warning(f"Password verification error: {type(e).__name__}")
        return False

def get_password_hash(password: str) -> str:
    """Hashes a raw password securely using direct bcrypt."""
    pwd_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode('utf-8')

def encrypt_credential(plain_text: str) -> str:
    """Encrypts sensitive database connection passwords or tokens using AES-256 (Fernet)."""
    if not plain_text:
        return ""
    encrypted_bytes = fernet.encrypt(plain_text.encode())
    return encrypted_bytes.decode()

def decrypt_credential(encrypted_text: str) -> str:
    """Decrypts AES-256 Fernet encrypted credentials."""
    if not encrypted_text:
        return ""
    try:
        decrypted_bytes = fernet.decrypt(encrypted_text.encode())
        return decrypted_bytes.decode()
    except Exception as e:
        logger.warning(f"Credential decryption error: {type(e).__name__}")
        return ""

import uuid

def create_access_token(
    subject: Union[str, Any],
    expires_delta: Optional[timedelta] = None,
    jti: Optional[str] = None
) -> str:
    """Creates a JWT access token with unique JTI for user session tracking."""
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    token_jti = jti or str(uuid.uuid4())
    to_encode = {
        "exp": expire,
        "sub": str(subject),
        "jti": token_jti
    }
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

def decode_token_payload(token: str) -> Optional[dict]:
    """Decodes JWT access token and returns full payload dictionary."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except Exception as e:
        logger.warning(f"Token decoding error: {type(e).__name__}")
        return None

def decode_access_token(token: str) -> Optional[str]:
    """Decodes JWT access token and returns user_id subject."""
    payload = decode_token_payload(token)
    return payload.get("sub") if payload else None


# Cuantos DIGITOS al final quedan visibles al enmascar una columna MASKED.
# Regla acordada: un RUT 12.345.678-9 se ve como *******678-9. Suficiente para que
# una persona compare dos registros a ojo, insuficiente para reconstruir el dato.
MASK_KEEP_LAST_DIGITS = 4


def mask_value(value, keep_last_digits: int = MASK_KEEP_LAST_DIGITS):
    """Enmascara un valor conservando los ultimos `keep_last_digits` DIGITOS.

    Se cuentan digitos, no caracteres: en "12.345.678-9" los ultimos 4 caracteres
    son "78-9" (pierde el 6), mientras que los ultimos 4 digitos son "678-9". La
    regla acordada es que el verificador y el cuerpo final queden visibles.
    Los separadores que quedan a la derecha del primer digito conservado se
    mantienen, para que el formato siga siendo reconocible.

    Se aplica DESPUES de ejecutar la consulta, en Python, y no reescribiendo el SQL:
    las funciones de cadena varian por dialecto, asi que hacerlo en SQL haria el
    enmascaramiento dialecto-dependiente y capaz de romper la consulta. Aquí no
    hay dialecto y no se puede romper nada.

    La columna sigue existiendo en el SELECT (por eso MASKED no se agrega a
    blocked_columns): la consulta funciona, el dato no sale.
    """
    if value is None:
        return None
    text = str(value)
    if keep_last_digits <= 0:
        return "*" * len(text)

    digit_positions = [i for i, ch in enumerate(text) if ch.isdigit()]
    if len(digit_positions) < keep_last_digits:
        # Fail-closed. Si no hay digitos suficientes no se puede aplicar la regla
        # acordada, y conservar los ultimos caracteres equivaldria a revelar el
        # final del dato en una columna marcada como sensible. Se tapa entero.
        return "*" * len(text)

    cut = digit_positions[-keep_last_digits]
    return "*" * cut + text[cut:]


# Prefijos que Excel/Sheets/LibreOffice interpretan como inicio de formula.
# Un valor de texto de la base del cliente que empieza por '=' se convierte en una
# formula al abrir el archivo, y ahi se puede meter HYPERLINK, WEBSERVICE o una
# referencia a otro archivo. Se antepone un apostrofo, que es el escape estandar
# de estos formatos y no altera el texto que ve el usuario.
SPREADSHEET_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_spreadsheet_value(value):
    """Neutraliza inyeccion de formulas al exportar a Excel o CSV.

    Aplicarlo SIEMPRE a datos que vienen de la base del cliente, no solo a
    entrada de usuario: basta con que una columna de texto (nombre_cliente,
    observaciones, comentario) empiece por '=' para que se convierta en formula
    cuando un C-Level abre el .xlsx.
    """
    if isinstance(value, str) and value[:1] in SPREADSHEET_FORMULA_PREFIXES:
        return "'" + value
    return value


def mask_rows(rows, masked_columns) -> int:
    """Enmascara in-place las columnas MASKED de una lista de filas. Devuelve cuantas toco.

    `masked_columns` es un set de nombres de columna en minuscula, igual que
    `blocked_columns`. Se compara sin distinguir mayusculas porque el motor puede
    devolver las claves con el casing con el que estan escritas en la consulta.
    """
    if not masked_columns or not rows:
        return 0
    lowered = {c.lower() for c in masked_columns}
    touched = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in list(row.keys()):
            if key.lower() in lowered:
                row[key] = mask_value(row[key])
                touched += 1
    return touched


def _masked_output_keys(sql: str, masked_lower: set, dialect: str = "postgres"):
    """Aliases de salida que provienen de una columna MASKED.

    `mask_rows` compara la CLAVE de la fila con el nombre de la columna. Eso deja
    pasar `SELECT rut_dni_cliente AS identificador`: la clave es
    `identificador`, no `rut_dni_cliente`. Devuelve (aliases, mask_all):
    `mask_all=True` cuando una proyeccion usa una columna MASKED y no tiene alias,
    porque en ese caso no hay una clave estable que podamos identificar.
    """
    if not sql or not sql.strip():
        return set(), False
    try:
        from sqlglot import exp, parse_one
    except Exception:
        return set(), False

    read = dialect.lower() if dialect and dialect.lower() in {"postgres", "tsql", "mysql", "oracle"} else "postgres"
    try:
        expression = parse_one(sql.strip().rstrip(";"), read=read)
    except Exception:
        return set(), False
    if expression is None:
        return set(), False

    # `perfil['rut']` es un subscript JSONB (PostgreSQL 14+), no un
    # JSONExtract. El AST lo rechaza, pero si algun caller futuro le pasa SQL
    # sin pasar por el validador, el sink no debe dejar el escalar en claro.
    # Sin tipos no se distingue de `arr[1]`; se enmascara toda la fila.
    if expression.find(exp.Bracket) is not None:
        return set(), True

    projections = []
    for select_node in expression.find_all(exp.Select):
        projections.extend(select_node.expressions)

    # Punto fijo: un alias de una columna MASKED es, a su vez, una fuente
    # MASKED para las proyecciones que lo consumen. Sin propagarlo,
    # `WITH x AS (SELECT rut AS r ...) SELECT MAX(r) AS max_rut ...` dejaba
    # `max_rut` en claro: la proyeccion externa solo menciona `r`, no `rut`.
    masked_names = set(masked_lower)
    mask_all = False
    changed = True
    while changed:
        changed = False
        for projection in projections:
            columns = list(projection.find_all(exp.Column))
            if isinstance(projection, exp.Column) and projection not in columns:
                columns.append(projection)
            if not any((col.name or "").lower() in masked_names for col in columns):
                continue
            alias = (projection.alias_or_name or "").strip()
            if alias:
                alias_lower = alias.lower()
                if alias_lower not in masked_names:
                    masked_names.add(alias_lower)
                    changed = True
            else:
                # Sin alias la clave real la decide el driver (p. ej. `sum` o
                # `?column?`). No hay forma segura de saber cual es, asi que el
                # llamador enmascara todos los valores de esa fila.
                mask_all = True

        # `WITH x (a) AS (...)`, `FROM (SELECT ...) d(a)` y `LATERAL (...) d(a)`
        # renombran columnas fuera de la proyeccion: el nombre MASKED original
        # ya no aparece en la consulta externa. Sin propagar la lista de alias,
        # un string compuesto sobre `a` quedaba en claro.
        alias_sources = []
        for cte in expression.find_all(exp.CTE):
            alias_sources.append((cte, cte.this))
        for subquery in expression.find_all(exp.Subquery):
            alias_sources.append((subquery, subquery.this))
        for lateral in expression.find_all(exp.Lateral):
            body = lateral.this
            if isinstance(body, exp.Subquery):
                body = body.this
            alias_sources.append((lateral, body))
        for owner, body in alias_sources:
            aliases = list(getattr(owner, "alias_column_names", None) or [])
            while isinstance(body, (exp.Subquery, exp.Union, exp.Paren)):
                body = body.this
            if not aliases or not isinstance(body, exp.Select):
                continue
            for index, projection in enumerate(body.expressions):
                if index >= len(aliases):
                    break
                columns = list(projection.find_all(exp.Column))
                if isinstance(projection, exp.Column) and projection not in columns:
                    columns.append(projection)
                if not any((col.name or "").lower() in masked_names for col in columns):
                    continue
                alias = (aliases[index] or "").strip()
                if alias and alias.lower() not in masked_names:
                    masked_names.add(alias.lower())
                    changed = True

        # `FROM tabla c(x)` renombra columnas fisicas sin dejar rastro del
        # nombre MASKED. Sin metadata del orden de columnas no se puede saber
        # cual alias corresponde a cual; enmascarar todos es fail-closed.
        for table in expression.find_all(exp.Table):
            for alias in list(getattr(table, "alias_column_names", None) or []):
                alias_lower = (alias or "").strip().lower()
                if alias_lower and alias_lower not in masked_names:
                    masked_names.add(alias_lower)
                    changed = True

        # `UNION`/`EXCEPT`/`INTERSECT` con lista de alias: la salida no se
        # puede mapear de forma segura solo con la rama izquierda (UNION puede
        # traer la columna MASKED por la derecha; EXCEPT/INTERSECT no son
        # exp.Union). Si cualquier rama usa una columna MASKED, se enmascara
        # toda la fila.
        for set_operation in expression.find_all(exp.SetOperation):
            if any(
                (col.name or "").lower() in masked_names
                for col in set_operation.find_all(exp.Column)
            ):
                mask_all = True
                break

        # Una tabla `VALUES` puede llevar una columna MASKED (p. ej. un
        # subselect o una referencia LATERAL). Sus nombres de salida son
        # posicionales y no hay un mapeo seguro sin metadata; enmascarar toda
        # la fila es fail-closed y cubre `v(x)` y `SELECT v`.
        for values in expression.find_all(exp.Values):
            if any(
                (col.name or "").lower() in masked_names
                for col in values.find_all(exp.Column)
            ):
                mask_all = True
                break

        # `UNNEST(columna_masked)` expande el array en filas con un alias que
        # tampoco deja rastro del nombre original. Mismo criterio fail-closed.
        for unnest in expression.find_all(exp.Unnest):
            if any(
                (col.name or "").lower() in masked_names
                for col in unnest.find_all(exp.Column)
            ):
                mask_all = True
                break
    return masked_names - masked_lower, mask_all


def _mask_masked_json(value, lowered):
    """Recorre dict/list/string JSON y enmascara las claves MASKED anidadas.

    Defensa en profundidad: una columna JSON puede traer el dato sensible dentro
    del documento (`{"sueldo": 123456}`). `mask_rows` no lo ve porque compara la
    CLAVE de la fila (el nombre de la columna JSON), no las claves del documento.
    Devuelve `(valor, cantidad_enmascarada)`; el valor puede ser un string nuevo
    cuando hubo que reserializar JSON.
    """
    if isinstance(value, dict):
        touched = 0
        for key in list(value.keys()):
            if str(key).lower() in lowered:
                value[key] = mask_value(value[key])
                touched += 1
            else:
                value[key], nested = _mask_masked_json(value[key], lowered)
                touched += nested
        return value, touched

    if isinstance(value, list):
        touched = 0
        for index, item in enumerate(value):
            value[index], nested = _mask_masked_json(item, lowered)
            touched += nested
        return value, touched

    if isinstance(value, tuple):
        # Un SELECT de una fila compuesta (o VALUES) llega como tuple. Sin
        # recorrerlo, un JSON con una clave MASKED adentro salia en claro.
        touched = 0
        items = []
        for item in value:
            item, nested = _mask_masked_json(item, lowered)
            items.append(item)
            touched += nested
        if touched:
            return tuple(items), touched
        return value, 0

    if isinstance(value, str):
        # Solo se intenta parsear lo que parece un objeto/array JSON. Un string
        # normal no se toca, y si el parseo falla se devuelve tal cual.
        if value.lstrip()[:1] in ("{", "["):
            try:
                parsed = json.loads(value)
            except (TypeError, ValueError):
                return value, 0
            if isinstance(parsed, (dict, list)):
                parsed, touched = _mask_masked_json(parsed, lowered)
                if touched:
                    return json.dumps(parsed, ensure_ascii=False), touched
                # Sin claves MASKED adentro se conserva el texto original: no se
                # reformatea un JSON que no habia que enmascarar.
                return value, 0

    return value, 0


def mask_query_rows(rows, masked_columns, sql: Optional[str] = None, dialect: str = "postgres") -> int:
    """Enmascara filas MASKED resolviendo tambien los alias del SELECT.

    Es el reemplazo de `mask_rows` en los caminos que tienen el SQL a mano. Sin
    SQL se comporta igual que `mask_rows`, para no romper callers viejos.
    Ademas recorre dict/list/JSON anidados: una columna JSON puede llevar el
    dato sensible adentro y la clave de la fila no lo delata.
    """
    if not masked_columns or not rows:
        return 0
    lowered = {str(c).lower() for c in masked_columns}
    aliases, mask_all = _masked_output_keys(sql or "", lowered, dialect)

    if mask_all:
        touched = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key in list(row.keys()):
                row[key] = mask_value(row[key])
                touched += 1
        return touched

    touched = mask_rows(rows, lowered | aliases)
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in list(row.keys()):
            row[key], nested = _mask_masked_json(row[key], lowered)
            touched += nested
    return touched

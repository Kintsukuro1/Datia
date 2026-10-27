import re
from typing import Set, Optional
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.constants import ADMIN_ROLES
from app.core.database import discard_failed_transaction
from app.modules.chat_engine.dynamic_schema import DynamicSchemaPruningService

# ---------------------------------------------------------------------------
# Dominios funcionales del guard de gobernanza.
#
# La autoridad es la MATRIZ RBAC, no el nombre del rol. Antes se clasificaba el
# rol por substring: "Administrativo de TI" caia en el bypass de admin por
# contener "admin", "Coordinador de Gestion" se trataba como infraestructura por
# el "ti" de "Gestion", y "Finance Analyst" no matcheaba ninguna rama y quedaba
# sin politica. Renombrar un rol desde el panel o crear uno nuevo lo dejaba en
# la misma situacion, sin aviso.
#
# Ahora la PREGUNTA se clasifica por su vocabulario y el permiso se resuelve
# contra las tablas que el rol tiene en `role_table_permissions`: si la pregunta
# toca un dominio del que el rol no tiene NI UNA tabla, se deniega. Un rol con
# `fact_ventas` puede preguntar por ventas se llame como se llame; uno sin
# tablas de finanzas no, aunque se apellide "Administrativo de TI".
#
# Las tablas se clasifican por SU nombre (no por el del rol) porque el dataset
# de un cliente no se va a llamar como el de la demo.
#
# Si NINGUNA tabla del rol se puede clasificar (un CSV subido como
# `movimientos`, `hoja1`, `datos_2024`), el guard NO deniega: no hay evidencia
# de que la matriz pertenezca a otro dominio, y negarle "ingresos" al duenio de
# esa unica tabla seria negarle su propio dato. En ese caso el gate duro es la
# matriz + el AST, que ya acotan las tablas y las columnas.
#
# ponytail: mapear vocabulario -> dominio es una heuristica de UX. El gate duro
# sigue siendo el AST (`ast_validator`): esta capa solo convierte un rechazo
# tardio en uno temprano y explicable. Si un cliente reporta un falso positivo,
# se amplia el vocabulario de aca; la matriz no se toca.
_FINANCE_TABLES_RE = re.compile(
    r"venta|ingreso|costo|factura|gasto|precio|margen|utilidad|saldo|balance|"
    r"cobro|pago|cliente|producto|categoria|comercial|rentabilidad"
)
_TI_TABLES_RE = re.compile(
    r"servidor|incidente|consumo|recurso|infra|cluster|nodo|telemetr|monitor|"
    r"alerta|ticket|soporte"
)
_PERSONAL_TABLES_RE = re.compile(
    r"emplead|persona|nomina|sueldo|salario|rrhh|talento|remunerac"
)

# `precio`, `tarifa` y `cotizacion` NO estan a proposito del lado financiero: el
# catalogo comercial (`dim_categorias`, `dim_productos`) es una lista de precios
# que la matriz declara consultable por perfiles sin acceso a la operacion, y
# negarles "cuanto cuesta el producto X" contradecia su propia matriz.
_FINANCE_QUESTION_RE = re.compile(
    r"\b(saldo|saldos|balance|balances)\b"
    r"|\b(venta|ventas|preventa|postventa)\b"
    r"|\b(ingreso|ingresos|recaudaci[oó]n|cobro|cobros)\b"
    r"|\b(ganancia|ganancias|lucro|utilidad|utilidades)\b"
    r"|\b(facturaci[oó]n|factura|facturas|facturado)\b"
    r"|\b(margen|m[aá]rgenes|ebitda|rentabilidad)\b"
    r"|\b(costo|costos|gasto|gastos|egreso|egresos|presupuesto|presupuestos)\b"
    r"|\b(dinero|monto|montos|financier[oa]s?|finanzas)\b"
    r"|\b(econ[oó]mic[oa]s?|econom[íi]a|comercial(es)?)\b"
    r"|\b(cartera\s+de\s+clientes|comprador|compradores)\b"
    r"|\b(m[oó]dulo|[aá]rea|departamento)\s+(de\s+)?(finanzas|financier[oa]|comercial|ventas|facturaci[oó]n|econom[íi]a)\b"
    r"|\b(fact_ventas|fact_ingresos_costos|vbak_cabpedidoventa|vbap_pospedidoventa|kna1_clientes)\b"
)

_TI_QUESTION_RE = re.compile(
    r"\b(tecnol[oó]gic[oa]s?|tecnolog[ií]a)\b"
    r"|\b(ti|it|t\.i\.)\b"
    r"|\b(inform[aá]tic[oa]s?|telecomunicaci[oó]n|telecomunicaciones)\b"
    r"|\b(servidor|servidores|server|servers|host|hosts|cluster|clusters|nodo|nodos)\b"
    r"|\b(cpu|memoria\s+ram|\bram\b|disco|discos|almacenamiento)\b"
    r"|\b(incidente|incidentes|incidentes\s+ti|incidentes_ti)\b"
    r"|\b(ticket|tickets|soporte\s+t[ée]cnico|helpdesk|mesa\s+de\s+ayuda)\b"
    r"|\b(uptime|downtime|ca[íi]da|ca[íi]das|disponibilidad\s+del?\s+sistema)\b"
    r"|\b(consumo\s+de\s+recursos|latencia|ancho\s+de\s+banda|ping|router|switch|firewall)\b"
    r"|\b(infraestructura(\s+de\s+ti|\s+tecnol[oó]gica|\s+t[ée]cnica)?)\b"
    r"|\b(m[oó]dulo|[aá]rea|departamento)\s+(de\s+)?(ti|it|tecnolog[íi]a|infraestructura|sistemas)\b"
    r"|\b(m[oó]dulo\s+(ti|it|tecnol[oó]gic[oa]))\b"
    r"|\b(sistemas\s+inform[aá]ticos|telemetr[íi]a)\b"
    r"|\b(parche|parches|vulnerabilidad|vulnerabilidades|ciberseguridad|seguridad\s+ti)\b"
    r"|\b(backup|backups|respaldo|respaldos)\b"
    r"|\b(dim_servidores|fact_incidentes_ti|fact_consumo_recursos)\b"
)

_HR_QUESTION_RE = re.compile(
    r"\b(sueldo|sueldos|salario|salarios|remuneraci[oó]n|remuneraciones)\b"
    r"|\b(n[oó]mina|n[oó]minas|honorario|honorarios)\b"
    r"|\b(cu[aá]nto\s+gana[n]?|compensaci[oó]n|compensaciones)\b"
    r"|\b(m[oó]dulo|[aá]rea|departamento)\s+(de\s+)?(rrhh|recursos humanos|talento|personal)\b"
)

# "Vendimos productos tecnologicos" es una pregunta COMERCIAL, no de
# infraestructura: el perfil de finanzas debe poder hacerla. Sin esta excepcion,
# la palabra "tecnologic" disparaba la rama TI y la matriz de finanzas no tiene
# ninguna tabla de TI. Es la misma excepcion que ya existia.
_PRODUCT_QUERY_RE = re.compile(
    r"\b(producto|productos|categor[íi]a|categor[íi]as|art[íi]culo|art[íi]culos|cat[aá]logo)\b"
)
_COMMERCIAL_CONTEXT_RE = re.compile(
    r"\b(vendid[oa]s?|vendieron|vendimos|vender|venta|ventas|comprad[oa]s?|compraron|"
    r"compramos|compras?|precio|precios|facturaci[oó]n|facturas?|cliente|clientes)\b"
)

# (nombre, regex de la pregunta, regex de las tablas del dominio, mensaje)
_DOMAIN_RULES = (
    (
        "finanzas",
        _FINANCE_QUESTION_RE,
        _FINANCE_TABLES_RE,
        "Gobernanza RBAC: Acceso denegado. El perfil '{role}' no tiene autorización "
        "para consultar información financiera, facturación, ventas, ingresos ni balances comerciales de la organización.",
    ),
    (
        "ti",
        _TI_QUESTION_RE,
        _TI_TABLES_RE,
        "Gobernanza RBAC: Acceso denegado. El perfil '{role}' no tiene autorización "
        "para consultar servidores, incidentes técnicos ni métricas de infraestructura TI.",
    ),
    (
        "personal",
        _HR_QUESTION_RE,
        _PERSONAL_TABLES_RE,
        "Gobernanza RBAC: Acceso denegado. El perfil '{role}' no tiene autorización "
        "para consultar salarios, remuneraciones, nóminas ni información confidencial de Recursos Humanos.",
    ),
)

class GovernanceGuard:
    """
    Enforces Fail-Closed RBAC table access, column masking, and cross-domain boundary isolation.
    """

    @classmethod
    def _schema_field_for_role(
        cls,
        field: str,
        user_role: str,
        is_admin: bool,
        db: Optional[Session],
        role_id: Optional[int],
        connection_id: Optional[int],
    ) -> Set[str]:
        """Campo del esquema autorizado para el rol, con politica fail-closed.

        Un solo cuerpo para `allowed_tables`, `blocked_columns` y
        `masked_columns`: los tres resuelven la misma sesion local y comparten
        la misma politica de error.

        Antes cualquier falla devolvia `set()`. Para `allowed_tables` eso deniega,
        pero para `blocked_columns`/`masked_columns` un set vacio es fail-OPEN:
        "ninguna columna bloqueada ni enmascarada", o sea los valores sensibles
        salen en claro. El set vacio tambien disfrazaba de RBAC un fallo de
        infraestructura. Ahora, si la matriz no se puede resolver, se lanza 503:
        una request sin matriz no es una request con cero restricciones. Las
        `HTTPException` deliberadas de la capa de abajo (un 404 de conexion, por
        ejemplo) se re-lanzan sin reescribirse.
        """
        local_db = None
        if db is None:
            try:
                from app.core.database import SessionLocal
                local_db = SessionLocal()
                active_db = local_db
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail="No se pudo resolver la matriz RBAC de permisos: no hay sesion de metadatos.",
                ) from exc
        else:
            active_db = db

        if active_db is None:
            raise HTTPException(
                status_code=503,
                detail="No se pudo resolver la matriz RBAC de permisos: no hay sesion de metadatos.",
            )

        try:
            schema_info = DynamicSchemaPruningService.get_authorized_schema_prompt(
                db=active_db,
                role_id=role_id,
                user_role=user_role,
                connection_id=connection_id,
                is_admin=is_admin
            )
            return schema_info.get(field, set())
        except HTTPException:
            # Un 404 de conexion no es "sin permisos": convertirlo en 503 (o en
            # set()) lo disfrazaba de RBAC y el motor respondia con el codigo
            # equivocado. Se re-lanza tal cual.
            raise
        except Exception as exc:
            # La sesion es del CALLER, no nuestra. Tragarnos el error sin
            # deshacer la transaccion abortada la deja muerta, y el siguiente
            # query de esa request falla con `InFailedSqlTransaction` — un error
            # sin relacion con lo que fallo. Fail-closed de verdad: la request
            # falla con 503 en vez de seguir sin restricciones de columna.
            discard_failed_transaction(active_db)
            raise HTTPException(
                status_code=503,
                detail="No se pudo resolver la matriz RBAC de permisos para este perfil.",
            ) from exc
        finally:
            if local_db is not None:
                local_db.close()

    @classmethod
    def get_allowed_tables_for_role(
        cls,
        user_role: str,
        is_admin: bool,
        db: Optional[Session] = None,
        role_id: Optional[int] = None,
        connection_id: Optional[int] = None
    ) -> Set[str]:
        if is_admin or user_role in ADMIN_ROLES:
            is_admin = True
        # Referencia explicita y no `cls.` a proposito: engine.py expone estos
        # metodos como fachada (`QueryEngine.get_allowed_tables_for_role = ...`)
        # y ahi `cls` llega re-bound a QueryEngine, que no tiene el helper.
        # El codigo original tampoco usaba `cls`; esto preserva esa semantica.
        return GovernanceGuard._schema_field_for_role("allowed_tables", user_role, is_admin, db, role_id, connection_id)

    @classmethod
    def get_blocked_columns_for_role(
        cls,
        user_role: str,
        is_admin: bool,
        db: Optional[Session] = None,
        role_id: Optional[int] = None,
        connection_id: int = 1
    ) -> Set[str]:
        if is_admin or user_role in ADMIN_ROLES:
            return set()
        return GovernanceGuard._schema_field_for_role("blocked_columns", user_role, is_admin, db, role_id, connection_id)

    @classmethod
    def get_masked_columns_for_role(
        cls,
        user_role: str,
        is_admin: bool,
        db: Optional[Session] = None,
        role_id: Optional[int] = None,
        connection_id: int = 1
    ) -> Set[str]:
        """Columnas MASKED cuyo VALOR debe ocultarse a este perfil.

        A diferencia de BLOCKED, estas columnas se pueden seguir consultando: se
        consulta el valor y se enmascara antes de que salga de la capa de ejecucion.
        Un admin las ve en claro.
        """
        if is_admin or user_role in ADMIN_ROLES:
            return set()
        return GovernanceGuard._schema_field_for_role("masked_columns", user_role, is_admin, db, role_id, connection_id)

    @classmethod
    def check_domain_governance(
        cls,
        question: str,
        user_role: str,
        allowed_tables: Set[str],
        is_admin: bool = False,
    ) -> Optional[str]:
        """Deniega la pregunta si toca un dominio del que el rol no tiene tablas.

        `is_admin` explicito y `ADMIN_ROLES` por nombre EXACTO: el bypass no se
        deduce de un substring. `"Administrativo de TI"` no es administrador.

        Devuelve el motivo del rechazo o `None` si la pregunta cae dentro de la
        matriz del rol.
        """
        # 0. El unico bypass es el admin explicito de la cuenta (`is_admin`) o el
        # rol EXACTO de `ADMIN_ROLES`. El C-Level NO esta en esa lista: lo que su
        # matriz no cubre se le deniega igual, como a cualquier otro rol.
        if is_admin or user_role in ADMIN_ROLES:
            return None

        q_lower = (question or "").lower().strip()
        if not q_lower:
            return None

        tables = {t.lower() for t in (allowed_tables or set())}

        # Dominios que la matriz del rol cubre, deducidos de los NOMBRES de sus
        # tablas. Vacio = no se puede clasificar ninguna (dataset con nombres
        # arbitrarios): no hay evidencia de cruce de dominio, se defiere al gate
        # duro. Denegar aca dejaria a un cliente con una tabla `movimientos` sin
        # poder preguntar por sus propios ingresos.
        role_domains = {
            domain
            for domain, _, table_re, _ in _DOMAIN_RULES
            if any(table_re.search(table) for table in tables)
        }
        if not role_domains:
            return None

        is_product_query = bool(
            _PRODUCT_QUERY_RE.search(q_lower)
            and _COMMERCIAL_CONTEXT_RE.search(q_lower)
        )

        for domain, question_re, _, denial in _DOMAIN_RULES:
            if domain == "ti" and is_product_query:
                continue
            if domain in role_domains:
                continue
            if question_re.search(q_lower):
                return denial.format(role=user_role)

        return None

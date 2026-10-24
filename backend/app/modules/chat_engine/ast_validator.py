from typing import List, Set, Dict, Tuple, Optional, Any
from sqlglot import exp, parse_one
from app.core.config import settings
from app.core.logging import logger

class ASTValidationError(Exception):
    """Custom exception raised when SQL fails AST security or RBAC validation."""
    pass

class ASTValidator:
    """
    SQL Guardrail & AST Security Parser based on sqlglot.
    Enforces read-only SELECT queries, single statement execution,
    role-based table/column whitelisting, Star expansion for Column-Level Security (CLS),
    and automatic LIMIT injection.
    """

    ALLOWED_DIALECTS = {"postgres", "tsql", "mysql", "oracle"}

    # Esquema por defecto de cada dialecto. El producto solo conecta
    # PostgreSQL (`DatabaseType.POSTGRESQL`), pero el parser acepta otros
    # dialectos y el chequeo no puede asumir `public` para todos. MySQL y
    # Oracle quedan sin calificador permitido: no hay un default seguro sin
    # conocer la conexión.
    DEFAULT_SCHEMAS = {
        "postgres": {"public"},
        "tsql": {"dbo"},
        "mysql": set(),
        "oracle": set(),
    }

    # Allowlist de funciones SQL de negocio para los nodos que sqlglot no sabe
    # tipar y representa como `exp.Anonymous` (AGE, MAKE_DATE, SPLIT_PART, ...).
    # Es una allowlist y no una denylist: lo que no figura aca se rechaza.
    # Quedan afuera, a proposito, las funciones de sistema de PostgreSQL que
    # permiten leer archivos, dormir la sesion o tocar la configuracion:
    # `pg_read_file`, `pg_ls_dir`, `pg_sleep`, `current_setting`, `set_config`,
    # `dblink*`, `query_to_xml`, `lo_*`, `row_to_json`, `to_json*`, `json_*`,
    # `array_to_json`, etc.
    ALLOWED_FUNCTIONS = frozenset({
        # Agregados
        "sum", "avg", "count", "min", "max", "count_big", "any_value",
        "bool_and", "bool_or", "every", "stddev", "stddev_pop", "stddev_samp",
        "variance", "var_pop", "var_samp", "median", "mode", "string_agg",
        "array_agg", "group_concat", "bit_and", "bit_or", "bit_xor",
        "corr", "covar_pop", "covar_samp", "percentile_cont", "percentile_disc",
        "regr_avgx", "regr_avgy", "regr_count", "regr_intercept", "regr_r2",
        "regr_slope", "regr_sxx", "regr_sxy", "regr_syy",
        # Cadenas
        "concat", "concat_ws", "format", "lower", "upper", "initcap", "length",
        "char_length", "character_length", "octet_length", "bit_length",
        "substring", "substr", "left", "right", "trim", "btrim", "ltrim",
        "rtrim", "lpad", "rpad", "repeat", "reverse", "replace", "translate",
        "split_part", "strpos", "position", "regexp_replace", "regexp_match",
        "regexp_matches", "regexp_split_to_array", "regexp_split_to_table",
        "starts_with", "md5",
        # Fecha y hora
        "now", "current_date", "current_time", "current_timestamp", "localtime",
        "localtimestamp", "to_char", "to_date", "to_timestamp", "date_trunc",
        "date_part", "extract", "age", "make_date", "make_time",
        "make_timestamp", "make_timestamptz", "make_interval", "timezone",
        "justify_days", "justify_hours", "justify_interval", "isfinite",
        "clock_timestamp", "statement_timestamp", "transaction_timestamp",
        # Matematicas
        "abs", "ceil", "ceiling", "floor", "round", "trunc", "sign", "mod",
        "power", "pow", "sqrt", "cbrt", "exp", "ln", "log", "log10", "log2",
        "degrees", "radians", "pi", "random", "greatest", "least", "div",
        "width_bucket", "gcd", "lcm", "factorial",
        # Ventana
        "row_number", "rank", "dense_rank", "percent_rank", "cume_dist",
        "ntile", "lag", "lead", "first_value", "last_value", "nth_value",
        # Condicionales
        "coalesce", "nullif", "ifnull", "nvl", "nvl2", "isnull", "if", "iif",
        # Arreglos (sin tocar JSON)
        "array_length", "array_lower", "array_upper", "cardinality",
        "array_position", "array_positions", "array_remove", "array_replace",
        "array_cat", "array_append", "array_prepend", "array_fill",
        "array_to_string", "string_to_array", "unnest",
    })

    # Nodos JSON que sqlglot tipa y que, aunque no pasan por la allowlist de
    # `exp.Anonymous`, extraen claves/valores (o arman filas completas) y son
    # via de salida para datos no proyectados. El sink de masking enmascara
    # claves anidadas, pero no puede saber que `perfil->>'rut'` devuelve el
    # valor de una columna MASKED: por eso los operadores `->`, `->>`, `#>`,
    # `#>>` y sus funciones equivalentes se rechazan en el AST.
    DENIED_FUNCTION_NODES = tuple(
        getattr(exp, name)
        for name in (
            "JSONArrayAgg", "JSONObjectAgg",
            "JSONExtract", "JSONExtractScalar", "JSONExtractArray", "JSONExtractQuote",
            "JSONBExtract", "JSONBExtractScalar",
            "JSONKeys", "JSONKeysAtDepth", "JSONValueArray",
        )
        if hasattr(exp, name)
    )

    # PostgreSQL 14+ permite `perfil['rut']` como subscript de JSONB. sqlglot
    # lo representa como `exp.Bracket`, no como los nodos JSON de arriba, asi
    # que se colaba: el AST solo veia `perfil` y el sink no podia enmascarar el
    # escalar extraido. Sin metadatos de tipos no se puede distinguir de un
    # subindice de array normal (`arr[1]`), y un elemento de array tambien
    # puede contener el dato sensible. Se rechaza todo subindice, fail-closed.
    DENIED_SUBSCRIPT_NODES = tuple(
        getattr(exp, name) for name in ("Bracket",) if hasattr(exp, name)
    )

    @classmethod
    def validate_and_secure_sql(
        cls,
        raw_sql: str,
        dialect: str = "postgres",
        allowed_tables: Optional[Set[str]] = None,
        blocked_columns: Optional[Set[str]] = None,
        table_columns: Optional[Dict[str, List[str]]] = None,
        max_limit: Optional[int] = None,
        is_admin: bool = False
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Validates raw_sql against strict security rules and returns:
        (is_valid: bool, sanitized_sql_or_error: str, metadata: dict)

        `max_limit` es el techo de filas del guardarrai y sale de
        `settings.DEFAULT_ROW_LIMIT`. Antes era el literal 500 aqui, con
        `settings` importado en la linea 3 y sin usarse: cambiar la variable de
        entorno no hacia nada. Todo SQL del chat, del predictor y del
        autodiagnostico pasa por esta funcion, asi que este es el unico lugar
        donde el limite se decide.

        El default es `None` y NO `settings.DEFAULT_ROW_LIMIT`: un default de
        parametro se evalua UNA vez, al importar el modulo, asi que un
        `patch.object(settings, "DEFAULT_ROW_LIMIT", X)` de un test no tendria
        ningun efecto sobre el. Resolverlo aqui lo lee en cada llamada.
        """
        max_limit = settings.DEFAULT_ROW_LIMIT if max_limit is None else max_limit
        expression, sqlglot_dialect = cls._parse_and_validate_readonly_query(
            raw_sql, dialect
        )

        # Rule 4: Extract and Validate Tables against RBAC Whitelist
        allowed_schemas = cls.DEFAULT_SCHEMAS.get(sqlglot_dialect, {"public"})
        for table_node in expression.find_all(exp.Table):
            table_name = table_node.name.lower()
            # Un Table sin nombre es una funcion en el FROM (generate_series, ...):
            # puede fabricar filas y esquiva el control de tablas fisicas.
            if not table_name:
                raise ASTValidationError(
                    f"Seguridad: No se permiten funciones en la cláusula FROM ('{table_node.sql()}')."
                )
            schema = (table_node.db or "").lower()
            if schema and schema not in allowed_schemas:
                allowed_text = (
                    " o de los esquemas " + ", ".join(f"'{s}'" for s in sorted(allowed_schemas))
                    if allowed_schemas
                    else ""
                )
                raise ASTValidationError(
                    f"Seguridad: Acceso a esquemas no autorizado ('{table_node.sql()}'). "
                    f"Para el dialecto '{sqlglot_dialect}' solo se permiten tablas sin esquema{allowed_text}."
                )
            if table_node.catalog:
                raise ASTValidationError(
                    f"Seguridad: No se permiten referencias calificadas con catálogo ('{table_node.sql()}')."
                )

        # Una CTE puede llamarse igual que una tabla fisica: restar todos los
        # nombres de CTE permitia que una CTE ocultara una tabla prohibida.
        # Solo es CTE si el nombre esta visible en ese punto y no es su propia
        # definicion. El helper devuelve tambien las tablas fisicas reales.
        cte_names, physical_extracted = cls._resolve_physical_tables(expression)

        # Administrators have total visibility over all data tables in the database
        if not is_admin and allowed_tables is not None:
            allowed_set = {t.lower() for t in allowed_tables}
            unauthorized_tables = physical_extracted - allowed_set
            if unauthorized_tables:
                raise ASTValidationError(
                    f"Gobernanza RBAC: Acceso denegado. No tienes permisos para acceder ni manejar estos datos. Tablas no autorizadas: {', '.join(sorted(unauthorized_tables))}."
                )

        # Rule 4.5: Expand SELECT * / table.* to explicit allowed columns
        cls._expand_projection_stars(expression, table_columns, blocked_columns)

        # Rule 4.6: Reject whole-row references anywhere in the query.
        cls._reject_whole_row_references(expression, cte_names)

        # Rule 4.7: Any Star left after the expansion must be COUNT(*).
        cls._reject_nested_stars(expression)

        # Rule 5: Extract and Validate Columns against Blocked List
        extracted_columns = cls._extract_and_reject_blocked_columns(
            expression, blocked_columns, is_admin
        )

        # Rule 6: Inject LIMIT / TOP if not present or exceeds max_limit.
        expression = cls._clamp_limit(expression, max_limit)

        sanitized_sql = expression.sql(dialect=sqlglot_dialect)

        # sqlglot emite la sentencia SIN terminador, y eso hace que el SQL que el
        # producto muestra y deja copiar no sea ejecutable tal cual: pegado en un
        # psql interactivo queda en bucle esperando `;` y no imprime nada — ni
        # filas, ni `(0 rows)`, ni error, solo el prompt de vuelta. Se lee como
        # "no hay datos" cuando en realidad la consulta nunca corrio.
        #
        # Se termina aqui, y no en el boton de copiar, para que tambien quede
        # bien en `audit_logs`, en la trazabilidad y en los exports PDF/Excel: la
        # verificacion a mano es justamente el caso de uso.
        #
        # Verificado que no rompe la ejecucion: PostgreSQL (psycopg) acepta
        # igual un `;` final en un SELECT.
        if not sanitized_sql.rstrip().endswith(";"):
            sanitized_sql = sanitized_sql.rstrip() + ";"

        metadata = {
            "tables_used": sorted(list(physical_extracted)),
            "columns_used": sorted(list(extracted_columns)),
            "limit_applied": max_limit,
            "dialect": sqlglot_dialect
        }

        return True, sanitized_sql, metadata

    @classmethod
    def _parse_and_validate_readonly_query(
        cls, raw_sql: str, dialect: str
    ) -> Tuple[exp.Expression, str]:
        """
        Rules 1-3.5: cadena vacia, punto y coma, dialecto, parseo, solo SELECT,
        nodos prohibidos, allowlist de funciones y rechazo de subindices.
        Devuelve el AST y el dialecto normalizado de sqlglot.
        """
        if not raw_sql or not raw_sql.strip():
            raise ASTValidationError("La consulta SQL está vacía.")

        clean_sql = raw_sql.strip()

        # Rule 1: Single Statement Check (Reject semicolon chaining)
        if clean_sql.endswith(";"):
            clean_sql = clean_sql[:-1].strip()

        if ";" in clean_sql:
            raise ASTValidationError("Seguridad: Se prohíbe el encadenamiento de múltiples sentencias SQL (;).")

        # Determine sqlglot dialect
        sqlglot_dialect = dialect.lower()
        if sqlglot_dialect not in cls.ALLOWED_DIALECTS:
            sqlglot_dialect = "postgres"

        # Rule 2: Parse AST using sqlglot
        try:
            expression = parse_one(clean_sql, read=sqlglot_dialect)
        except Exception as e:
            raise ASTValidationError(f"Error de sintaxis SQL: No se pudo analizar el árbol AST ({str(e)}).")

        if expression is None:
            raise ASTValidationError("Error sintáctico: Estructura SQL no válida.")

        # Rule 3: Strictly SELECT statements only
        if not isinstance(expression, exp.Select):
            raise ASTValidationError(
                f"Seguridad: Operación prohibida. Únicamente se permiten consultas SELECT (Lectura). "
                f"Tipo detectado: {expression.key.upper()}."
            )

        # Ensure no DDL/DML mutation keywords exist anywhere in AST.
        # `Into` es SELECT INTO (crea y escribe una tabla) y `Lock` es
        # FOR UPDATE/SHARE (toma locks de escritura): los dos son mutacion
        # aunque el nodo raiz sea un SELECT.
        prohibited_expressions = tuple(
            getattr(exp, name) for name in (
                "Insert", "Update", "Delete", "Drop", "Create", "Alter", "Command", "Grant",
                "Into", "Lock",
            )
            if hasattr(exp, name)
        )
        for node in expression.walk():
            if isinstance(node, prohibited_expressions):
                raise ASTValidationError(f"Seguridad: Detectada instrucción prohibida '{node.key.upper()}'.")

        # Rule 3.5: function allowlist and schema-qualified function rejection.
        # `pg_catalog.pg_read_file('...')` se parsea como Dot(Identifier, Anonymous):
        # el nombre queda sin esquema, asi que la calificacion se detecta en el Dot.
        for dot_node in expression.find_all(exp.Dot):
            if isinstance(dot_node.expression, exp.Func):
                raise ASTValidationError(
                    f"Seguridad: No se permiten funciones calificadas con esquema o base de datos "
                    f"('{dot_node.sql()}')."
                )
        for func_node in expression.find_all(exp.Anonymous):
            func_name = (func_node.name or "").lower()
            if func_name not in cls.ALLOWED_FUNCTIONS:
                raise ASTValidationError(
                    f"Seguridad: Función no permitida '{func_name or func_node.sql()}'. "
                    f"Solo se autorizan funciones de negocio de la lista blanca."
                )
        if cls.DENIED_FUNCTION_NODES:
            for func_node in expression.find_all(*cls.DENIED_FUNCTION_NODES):
                raise ASTValidationError(
                    f"Seguridad: Función no permitida '{func_node.sql()}'. "
                    f"Solo se autorizan funciones de negocio de la lista blanca."
                )

        if cls.DENIED_SUBSCRIPT_NODES:
            for subscript_node in expression.find_all(*cls.DENIED_SUBSCRIPT_NODES):
                raise ASTValidationError(
                    f"Seguridad: No se permite el acceso por subíndice ('{subscript_node.sql()}'). "
                    f"Puede extraer valores de una columna JSON o de un array sin pasar por el "
                    f"control de columnas; usa una columna explícita o una función autorizada."
                )

        return expression, sqlglot_dialect

    @staticmethod
    def _clamp_limit(expression, max_limit: int):
        """
        Rule 6: inject LIMIT / TOP if absent and clamp every Limit in the tree.

        The clamp is a ceiling, never a floor. sqlglot >=30 exposes
        Literal.this as a read-only property, so nodes are replaced, not mutated.
        """
        for limit_node in expression.find_all(exp.Limit):
            try:
                current_val = int(limit_node.expression.this)
            except Exception:
                current_val = None
            if current_val is None or current_val > max_limit:
                limit_node.set("expression", exp.Literal.number(max_limit))

        if expression.args.get("limit") is None:
            expression = expression.limit(max_limit)

        return expression

    @staticmethod
    def _extract_and_reject_blocked_columns(
        expression,
        blocked_columns: Optional[Set[str]],
        is_admin: bool,
    ) -> Set[str]:
        """Rule 5: extract projected columns and reject blocked ones."""
        extracted_columns = set()
        for column_node in expression.find_all(exp.Column):
            if not isinstance(column_node.this, exp.Star):
                col_name = column_node.name.lower()
                if col_name:
                    extracted_columns.add(col_name)

        if not is_admin and blocked_columns is not None:
            blocked_set = {c.lower() for c in blocked_columns}
            attempted_blocked = extracted_columns & blocked_set
            if attempted_blocked:
                raise ASTValidationError(
                    f"Gobernanza RBAC: Acceso denegado. No tienes permisos para acceder ni manejar estos datos. Columnas bloqueadas: {', '.join(sorted(attempted_blocked))}."
                )

        return extracted_columns

    @staticmethod
    def _reject_nested_stars(expression) -> None:
        """
        Rule 4.7: any Star left after the expansion must be COUNT(*).

        `SELECT *` / `t.*` ya se expandieron arriba; un Star que sobrevive esta
        anidado dentro de una funcion o constructor (`ROW(t.*)`) y devuelve
        todas las columnas sin pasar por la expansion ni la CLS.
        """
        for star_node in expression.find_all(exp.Star):
            ancestor = star_node.parent
            inside_count = False
            while ancestor is not None:
                if isinstance(ancestor, exp.Count):
                    inside_count = True
                    break
                ancestor = ancestor.parent
            if not inside_count:
                raise ASTValidationError(
                    "Seguridad: Uso de '*' no permitido fuera de COUNT(*). "
                    "Selecciona las columnas explícitas."
                )

    @staticmethod
    def _walk_parents(node):
        parent = node.parent
        while parent is not None:
            yield parent
            parent = parent.parent

    @classmethod
    def _is_ancestor(cls, ancestor, node) -> bool:
        return any(parent is ancestor for parent in cls._walk_parents(node))

    @classmethod
    def _nearest_cte(cls, node):
        for parent in cls._walk_parents(node):
            if isinstance(parent, exp.CTE):
                return parent
        return None

    @classmethod
    def _nearest_select(cls, node):
        for parent in cls._walk_parents(node):
            if isinstance(parent, exp.Select):
                return parent
        return None

    @classmethod
    def _is_cte_reference(cls, table_node, cte) -> bool:
        """True si `table_node` referencia esta CTE y no una tabla fisica."""
        # Una referencia calificada con esquema va directo a la tabla fisica.
        if table_node.db or table_node.catalog:
            return False
        with_node = cte.parent
        if not isinstance(with_node, exp.With):
            return False
        scope = with_node.parent
        if scope is None or not cls._is_ancestor(scope, table_node):
            return False
        # Una CTE no se ve a si misma (salvo WITH RECURSIVE; fail-closed).
        if cte.this is table_node or (
            cte.this is not None and cls._is_ancestor(cte.this, table_node)
        ):
            return False
        nearest = cls._nearest_cte(table_node)
        if nearest is None:
            return True
        nearest_with = nearest.parent
        nearest_scope = nearest_with.parent if isinstance(nearest_with, exp.With) else None
        if nearest_scope is not scope:
            # Una CTE externa sigue visible desde una CTE anidada, y una
            # interna con el mismo nombre la sombrea: en ambos casos la
            # referencia es a una CTE, no a una tabla fisica.
            return True
        siblings = list(with_node.expressions)
        try:
            return siblings.index(cte) < siblings.index(nearest)
        except ValueError:
            return False

    @classmethod
    def _visible_cte(cls, table_node, ctes_by_name: Dict[str, List[Any]]):
        """(es_referencia_a_cte, cte). Si hay ambiguedad, cte=None."""
        table_name = (table_node.name or "").lower()
        matches = [
            cte
            for cte in ctes_by_name.get(table_name, ())
            if cls._is_cte_reference(table_node, cte)
        ]
        if not matches:
            return False, None
        return True, matches[0] if len(matches) == 1 else None

    @classmethod
    def _resolve_physical_tables(cls, expression) -> Tuple[Set[str], Set[str]]:
        """
        Resuelve cada referencia de tabla contra las CTE visibles en ese punto.

        Una CTE con el mismo nombre que una tabla fisica no la oculta: se
        devuelven los nombres de CTE (para la regla de fila completa) y el
        conjunto de tablas fisicas reales (para el RBAC y la metadata).
        """
        # Una CTE puede llamarse igual que una tabla fisica. Restar TODOS los
        # nombres de CTE del conjunto de tablas permitia el bypass:
        #   WITH secreto AS (SELECT * FROM public.secreto) SELECT * FROM secreto
        # El nombre "secreto" desaparecia del control RBAC y la CTE leia la
        # tabla fisica prohibida. Se resuelve cada referencia: solo es CTE si
        # el nombre esta visible en ese punto y no es la definicion de si misma.
        cte_names = {
            cte.alias_or_name.lower()
            for cte in expression.find_all(exp.CTE)
            if cte.alias_or_name
        }

        ctes_by_name: Dict[str, List[Any]] = {}
        for cte in expression.find_all(exp.CTE):
            cte_name = (cte.alias_or_name or "").lower()
            if cte_name:
                ctes_by_name.setdefault(cte_name, []).append(cte)

        physical_extracted = set()
        for table_node in expression.find_all(exp.Table):
            table_name = (table_node.name or "").lower()
            if not table_name:
                continue
            is_cte, _ = cls._visible_cte(table_node, ctes_by_name)
            if is_cte:
                continue
            physical_extracted.add(table_name)

        return cte_names, physical_extracted

    @staticmethod
    def _direct_sources(select_node) -> List[Any]:
        """Fuentes del FROM/JOIN de ESTE SELECT, sin bajar a subconsultas.

        `select_node.find_all(exp.Table)` atraviesa los ámbitos internos: en
        `WITH x AS (SELECT id FROM empleados) SELECT * FROM x` devolvía también
        `empleados` y expandía el `*` externo con columnas que la CTE no expone.
        """
        sources: List[Any] = []
        from_node = select_node.args.get("from_") or select_node.args.get("from")
        if from_node is not None:
            if isinstance(from_node, exp.From):
                if from_node.this is not None:
                    sources.append(from_node.this)
                sources.extend(from_node.expressions or [])
            else:
                sources.append(from_node)
        for join in select_node.args.get("joins") or []:
            if join.this is not None:
                sources.append(join.this)
        return sources

    @staticmethod
    def _leftmost_select(node):
        """SELECT que define los nombres de salida de un cuerpo (o de su UNION)."""
        while isinstance(node, exp.Union):
            node = node.this
        return node if isinstance(node, exp.Select) else None

    @staticmethod
    def _source_qualifier(source) -> Optional[str]:
        if isinstance(source, exp.Table):
            return (source.alias or source.name or "").lower() or None
        if isinstance(source, exp.Subquery):
            return (source.alias or "").lower() or None
        return None

    @classmethod
    def _source_columns(
        cls,
        source,
        physical_columns: Dict[str, List[str]],
        ctes_by_name: Dict[str, List[Any]],
        cte_outputs: Dict[int, List[str]],
        blocked_set: Set[str],
        seen: Set[int],
    ) -> Optional[List[str]]:
        """Columnas visibles de una fuente, o None si no se pueden resolver."""
        if isinstance(source, exp.Table):
            name = (source.name or "").lower()
            if not name:
                return None
            is_cte, cte = cls._visible_cte(source, ctes_by_name)
            if is_cte:
                if cte is None:
                    return None
                columns = cte_outputs.get(id(cte))
                return list(columns) if columns is not None else None
            physical = physical_columns.get(name)
            if physical is None:
                return None
            return [c for c in physical if c.lower() not in blocked_set]
        if isinstance(source, exp.Subquery):
            if not source.alias:
                return None
            select_node = cls._leftmost_select(source.this)
            if select_node is None:
                return None
            return cls._select_output_columns(
                select_node,
                physical_columns,
                ctes_by_name,
                cte_outputs,
                blocked_set,
                seen,
            )
        return None

    @classmethod
    def _select_output_columns(
        cls,
        select_node,
        physical_columns: Dict[str, List[str]],
        ctes_by_name: Dict[str, List[Any]],
        cte_outputs: Dict[int, List[str]],
        blocked_set: Set[str],
        seen: Set[int],
    ) -> Optional[List[str]]:
        """Nombres de salida de un SELECT, o None si alguno no es estable."""
        if id(select_node) in seen:
            return None
        seen = seen | {id(select_node)}
        sources = cls._direct_sources(select_node)
        outputs: List[str] = []
        for projection in select_node.expressions:
            if isinstance(projection, exp.Star):
                if not sources:
                    return None
                for source in sources:
                    columns = cls._source_columns(
                        source,
                        physical_columns,
                        ctes_by_name,
                        cte_outputs,
                        blocked_set,
                        seen,
                    )
                    if columns is None:
                        return None
                    outputs.extend(columns)
            elif isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star):
                target = (projection.table or "").lower()
                match = next(
                    (s for s in sources if cls._source_qualifier(s) == target),
                    None,
                )
                if match is None:
                    return None
                columns = cls._source_columns(
                    match,
                    physical_columns,
                    ctes_by_name,
                    cte_outputs,
                    blocked_set,
                    seen,
                )
                if columns is None:
                    return None
                outputs.extend(columns)
            elif isinstance(projection, exp.Alias):
                name = (projection.alias or "").strip()
                if not name:
                    return None
                outputs.append(name)
            elif isinstance(projection, exp.Column):
                name = (projection.name or "").strip()
                if not name:
                    return None
                outputs.append(name)
            else:
                # Sin alias estable (p. ej. COUNT(*) o una expresión) el driver
                # decide el nombre; no se puede expandir con seguridad.
                return None
        return outputs

    @classmethod
    def _build_cte_outputs(
        cls,
        expression,
        physical_columns: Dict[str, List[str]],
        blocked_set: Set[str],
    ) -> Tuple[Dict[int, List[str]], Dict[str, List[Any]]]:
        """Columnas de salida de cada CTE, resueltas antes de mutar el AST.

        Se indexan por `id(cte)`, no por nombre: dos CTE con el mismo nombre en
        ámbitos distintos pueden tener columnas distintas, y confundirlas
        expandiría un `*` físico con las columnas de una CTE que no está en su
        alcance.
        """
        ctes = list(expression.find_all(exp.CTE))
        ctes_by_name: Dict[str, List[Any]] = {}
        for cte in ctes:
            name = (cte.alias_or_name or "").lower()
            if name:
                ctes_by_name.setdefault(name, []).append(cte)
        outputs: Dict[int, List[str]] = {}
        # Punto fijo: `b` puede leer de `a` sin importar el orden del WITH.
        for _ in range(len(ctes) + 1):
            progressed = False
            for cte in ctes:
                if id(cte) in outputs:
                    continue
                select_node = cls._leftmost_select(cte.this)
                if select_node is None:
                    continue
                columns = cls._select_output_columns(
                    select_node,
                    physical_columns,
                    ctes_by_name,
                    outputs,
                    blocked_set,
                    set(),
                )
                if columns is None:
                    continue
                aliases = list(getattr(cte, "alias_column_names", None) or [])
                if aliases:
                    if len(aliases) > len(columns):
                        continue
                    columns = [str(a) for a in aliases] + columns[len(aliases):]
                outputs[id(cte)] = columns
                progressed = True
            if not progressed:
                break
        return outputs, ctes_by_name

    @classmethod
    def _expand_projection_stars(
        cls,
        expression,
        table_columns: Optional[Dict[str, List[str]]],
        blocked_columns: Optional[Set[str]],
    ) -> None:
        """Rule 4.5: expand SELECT * / table.* to explicit allowed columns.

        La expansión se hace por ámbito: cada SELECT usa solo sus fuentes
        directas (FROM/JOIN) y resuelve CTEs y subconsultas por sus columnas de
        salida. Así `SELECT * FROM x` no puede tomar columnas de una tabla
        física escondida dentro de la CTE.
        """
        # A projection star (SELECT * / tabla.*) cannot be filtered without column metadata,
        # so without table_columns we fail CLOSED instead of letting the star through untouched.
        # COUNT(*) is an aggregate, not a projection star, and reveals no column.
        projection_star = any(
            isinstance(item, exp.Star)
            or (isinstance(item, exp.Column) and isinstance(item.this, exp.Star))
            for select_node in expression.find_all(exp.Select)
            for item in select_node.expressions
        )
        if projection_star and not table_columns:
            raise ASTValidationError(
                "Seguridad: No se puede expandir 'SELECT *' porque no hay información de columnas "
                "disponible para esta conexión. Escribe las columnas explícitas "
                "(ej. SELECT id, monto FROM fact_ventas) o pide al administrador que publique el catálogo."
            )

        if expression.find(exp.Star) is None:
            return
        if not table_columns:
            logger.warning("CLS: SELECT * validado sin expansión de esquema, no se puede garantizar bloqueo de columnas")
            return

        norm_table_cols: Dict[str, List[str]] = {
            t.lower(): [str(c) for c in cols]
            for t, cols in table_columns.items()
        }
        blocked_set = {c.lower() for c in blocked_columns} if blocked_columns else set()
        cte_outputs, ctes_by_name = cls._build_cte_outputs(
            expression, norm_table_cols, blocked_set
        )
        unresolved_error = (
            "Gobernanza RBAC: Acceso denegado. No tienes columnas autorizadas para visualizar en la consulta."
        )

        for select_node in expression.find_all(exp.Select):
            sources = cls._direct_sources(select_node)
            resolved: List[Tuple[Optional[str], Optional[List[str]]]] = []
            for source in sources:
                resolved.append(
                    (
                        cls._source_qualifier(source),
                        cls._source_columns(
                            source,
                            norm_table_cols,
                            ctes_by_name,
                            cte_outputs,
                            blocked_set,
                            set(),
                        ),
                    )
                )

            new_expressions = []
            expanded_any_star = False

            for expr_item in select_node.expressions:
                is_simple_star = isinstance(expr_item, exp.Star)
                is_col_star = (
                    isinstance(expr_item, exp.Column)
                    and isinstance(expr_item.this, exp.Star)
                )

                if is_simple_star:
                    expanded_any_star = True
                    if not resolved:
                        raise ASTValidationError(unresolved_error)
                    for qualifier, columns in resolved:
                        if not columns:
                            raise ASTValidationError(unresolved_error)
                        for column in columns:
                            if len(resolved) > 1 and qualifier:
                                new_expressions.append(
                                    exp.Column(
                                        this=exp.to_identifier(column),
                                        table=exp.to_identifier(qualifier),
                                    )
                                )
                            else:
                                new_expressions.append(
                                    exp.Column(this=exp.to_identifier(column))
                                )

                elif is_col_star:
                    expanded_any_star = True
                    target_ref = (expr_item.table or "").lower()
                    target_columns = next(
                        (cols for qualifier, cols in resolved if qualifier == target_ref),
                        None,
                    )
                    if not target_columns:
                        raise ASTValidationError(unresolved_error)
                    for column in target_columns:
                        new_expressions.append(
                            exp.Column(
                                this=exp.to_identifier(column),
                                table=exp.to_identifier(expr_item.table),
                            )
                        )

                else:
                    new_expressions.append(expr_item)

            if expanded_any_star:
                if not new_expressions:
                    raise ASTValidationError(unresolved_error)
                select_node.set("expressions", new_expressions)


    @classmethod
    def _reject_whole_row_references(cls, expression, cte_names: Set[str]) -> None:
        """
        Rule 4.6: reject whole-row references anywhere in the query.

        `SELECT t FROM empleados t` devuelve la fila entera como un solo valor
        (incluidas las columnas bloqueadas) y sqlglot lo representa como una
        Column desnuda llamada `t`, no como un Star. Lo mismo pasa con
        `SELECT (t).sueldo` y con predicados como `WHERE t::text LIKE '%...%'`,
        que permiten inferir por fuerza bruta el valor de una columna bloqueada
        sin proyectarla. Por eso se revisa TODO el arbol, no solo el SELECT.

        El calificador NO vuelve segura la referencia: `public.empleados`,
        `empleados.empleados` y `t.empleados` (con `t` alias de `empleados`)
        tambien son fila completa. Se resuelve el calificador contra los
        alias/esquemas del query para no rechazar una columna normal de otra
        tabla que se llame igual que una tabla del JOIN.
        """
        row_reference_names = set(cte_names)
        alias_to_table: Dict[str, str] = {}
        schema_tables: Set[Tuple[str, str]] = set()
        # Una tabla derivada (`FROM (SELECT ...) d`) tambien es una referencia
        # de fila completa valida: `SELECT d` devuelve todas las columnas que
        # proyecta la subconsulta. No es un exp.Table, asi que sin esto se
        # escapaba del chequeo. El alias solo existe fuera de la subconsulta;
        # un `d` interno puede ser una columna normal y no se rechaza.
        # LATERAL deja la subquery colgando de `exp.Lateral`, no de Join.
        derived_aliases: Dict[str, List[Any]] = {}
        for subquery_node in expression.find_all(exp.Subquery):
            if not isinstance(subquery_node.parent, (exp.From, exp.Join)):
                continue
            derived_alias = (subquery_node.alias or "").lower()
            if derived_alias:
                derived_aliases.setdefault(derived_alias, []).append(subquery_node)
        for lateral_node in expression.find_all(exp.Lateral):
            derived_alias = (lateral_node.alias or "").lower()
            if derived_alias:
                derived_aliases.setdefault(derived_alias, []).append(lateral_node)
        # `FROM (VALUES ...) d` no es Subquery ni Table; `SELECT d` devuelve
        # la fila completa (un tuple) y se escapaba del chequeo.
        for values_node in expression.find_all(exp.Values):
            derived_alias = (values_node.alias or "").lower()
            if derived_alias:
                derived_aliases.setdefault(derived_alias, []).append(values_node)
        for table_node in expression.find_all(exp.Table):
            table_name = (table_node.name or "").lower()
            if table_name:
                row_reference_names.add(table_name)
                alias_to_table[table_name] = table_name
            table_alias = (table_node.alias or "").lower()
            if table_alias and table_name:
                row_reference_names.add(table_alias)
                alias_to_table[table_alias] = table_name
            if table_name and table_node.db:
                schema_tables.add((table_node.db.lower(), table_name))
        for column_node in expression.find_all(exp.Column):
            if isinstance(column_node.this, exp.Star):
                continue
            column_name = (column_node.name or "").lower()
            if not column_name:
                continue
            qualifier = (column_node.table or "").lower()
            if not qualifier:
                is_whole_row = column_name in row_reference_names
                if not is_whole_row:
                    for derived in derived_aliases.get(column_name, ()):
                        owner_select = cls._nearest_select(derived)
                        # El alias solo es visible en el SELECT que lo declara
                        # (o en sus subconsultas correlacionadas). Un alias
                        # homonimo en un subquery hermano no aplica.
                        if owner_select is None or not cls._is_ancestor(owner_select, column_node):
                            continue
                        # Dentro del cuerpo de la derivada, `d` no es el alias.
                        if cls._is_ancestor(derived, column_node):
                            continue
                        output_names = cls._derived_output_names(derived)
                        # Si la derivada proyecta una columna con ese nombre,
                        # `SELECT d` es esa columna, no la fila completa.
                        if output_names is not None and column_name in output_names:
                            continue
                        is_whole_row = True
                        break
            else:
                is_whole_row = (
                    qualifier == column_name
                    or alias_to_table.get(qualifier) == column_name
                    or (qualifier, column_name) in schema_tables
                )
            if is_whole_row:
                raise ASTValidationError(
                    f"Seguridad: Referencia a fila completa no permitida ('{column_node.sql()}'). "
                    f"Selecciona las columnas explícitas."
                )

    @staticmethod
    def _derived_output_names(node) -> Optional[Set[str]]:
        """Nombres de salida estables de una derivada; None si no se pueden afirmar.

        La lista de alias (`(...) d(x, y)`) renombra posicionalmente la salida:
        los nombres efectivos son `x, y`, no los del cuerpo. Ignorarla dejaba
        pasar `SELECT d` como si `d` fuera una columna cuando en realidad era
        la fila completa.
        """
        owner = node
        while isinstance(node, (exp.Lateral, exp.Subquery, exp.Union, exp.Paren)):
            node = node.this
        if isinstance(node, exp.Values):
            # VALUES no tiene nombres de columna estables; solo la lista de
            # alias declara la salida. Sin lista se falla cerrado.
            aliases = list(getattr(owner, "alias_column_names", None) or [])
            return {str(alias).lower() for alias in aliases} or None
        if not isinstance(node, exp.Select):
            return None
        names: List[str] = []
        for projection in node.expressions:
            if isinstance(projection, exp.Alias):
                name = (projection.alias or "").strip()
            elif isinstance(projection, exp.Column) and not isinstance(projection.this, exp.Star):
                name = (projection.name or "").strip()
            else:
                return None
            if not name:
                return None
            names.append(name)
        aliases = list(getattr(owner, "alias_column_names", None) or [])
        if aliases:
            if len(aliases) > len(names):
                return None
            names = [str(alias) for alias in aliases] + names[len(aliases):]
        return {name.lower() for name in names} or None

    @classmethod
    def extract_tables_from_query(cls, sql: str, dialect: str = "postgres") -> Set[str]:
        """
        Extracts physical table names from a SQL query using sqlglot AST,
        excluding CTE aliases. Fails safe and returns an empty set on parse error.
        """
        if not sql or not sql.strip():
            return set()
        sqlglot_dialect = dialect.lower() if dialect.lower() in cls.ALLOWED_DIALECTS else "postgres"
        try:
            expr = parse_one(sql.strip().rstrip(";"), read=sqlglot_dialect)
            if not expr:
                return set()
            tables = {t.name.lower() for t in expr.find_all(exp.Table) if t.name}
            ctes = {cte.alias_or_name.lower() for cte in expr.find_all(exp.CTE) if cte.alias_or_name}
            return tables - ctes
        except Exception:
            return set()

    @classmethod
    def generate_sql_explanation(cls, sql: str, dialect: str = "postgres") -> str:
        """
        Generates a human-friendly, plain-Spanish explanation of what the SQL query does
        using AST inspection without calling an LLM (Idea #39).
        """
        if not sql or not sql.strip():
            return "No se especificó ninguna consulta SQL."

        try:
            sqlglot_dialect = dialect.lower() if dialect.lower() in cls.ALLOWED_DIALECTS else "postgres"
            expr = parse_one(sql.strip().rstrip(";"), read=sqlglot_dialect)
            if not expr:
                return "Consulta analítica sobre la base de datos corporativa."

            parts = []

            # 1. Tables
            tables = sorted(list({t.name for t in expr.find_all(exp.Table) if t.name}))
            if tables:
                if len(tables) == 1:
                    parts.append(f"Consulta registros de la tabla **{tables[0]}**.")
                else:
                    joined_tables = ", ".join(f"**{t}**" for t in tables)
                    parts.append(f"Cruza información de las tablas {joined_tables}.")

            # 2. Aggregates & Projections
            aggregates = []
            for agg_cls, name in [
                (exp.Sum, "suma"),
                (exp.Avg, "promedio"),
                (exp.Count, "conteo"),
                (exp.Min, "mínimo"),
                (exp.Max, "máximo"),
            ]:
                for node in expr.find_all(agg_cls):
                    arg_str = node.this.sql() if hasattr(node, "this") else ""
                    aggregates.append(f"{name} de `{arg_str}`" if arg_str else name)

            if aggregates:
                parts.append(f"Calcula {', '.join(aggregates[:3])}.")

            # 3. Filters (WHERE)
            where_node = expr.args.get("where")
            if where_node:
                cond_sql = where_node.this.sql()
                if len(cond_sql) > 80:
                    cond_sql = cond_sql[:77] + "..."
                parts.append(f"Filtra por: `{cond_sql}`.")

            # 4. Grouping (GROUP BY)
            group_node = expr.args.get("group")
            if group_node and group_node.expressions:
                group_cols = [g.sql() for g in group_node.expressions]
                parts.append(f"Agrupa por `{', '.join(group_cols)}`.")

            # 5. Ordering (ORDER BY)
            order_node = expr.args.get("order")
            if order_node and order_node.expressions:
                order_cols = [o.sql() for o in order_node.expressions]
                parts.append(f"Ordena por `{', '.join(order_cols)}`.")

            # 6. Limit
            limit_node = expr.args.get("limit")
            if limit_node:
                parts.append(f"Limitado a {limit_node.expression.sql()} resultados.")

            return " ".join(parts) if parts else "Consulta de lectura directa sobre los registros corporativos."
        except Exception:
            return "Consulta estructurada de lectura ejecutada sobre la base de datos corporativa."


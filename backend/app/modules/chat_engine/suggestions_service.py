import re
from typing import Any, Dict, List, Optional, Set
from app.core.prompts import PromptManager
from app.modules.chat_engine.llm_service import LLMService


class SuggestionsService:
    """
    Generates role-tailored, schema-aware natural language question suggestions
    for citizen data democratization.

    Ademas de las sugerencias, expone `build_builder_options`: la misma metadata
    fisica del esquema (ya filtrada por RBAC y columnas BLOCKED) en una forma que
    el Smart Builder del frontend puede ofrecer sin adivinar nombres de columnas.
    """

    _NUMERIC_TYPE_TOKENS = (
        "int", "numeric", "decimal", "real", "double", "float", "money",
        "number", "serial", "bigint", "smallint",
    )
    _DATE_TYPE_TOKENS = ("date", "timestamp", "datetime")
    _DATE_NAME_TOKENS = (
        "fecha", "date", "periodo", "period", "anio", "año", "year",
        "trimestre", "quarter", "mes", "month", "dia", "day",
    )
    _IDENTIFIER_TOKENS = {
        "id", "key", "rut", "dni", "email", "correo", "telefono", "phone",
        "direccion", "address",
    }
    _IDENTIFIER_PREFIXES = {"numero", "number", "codigo", "code"}
    _FREE_TEXT_TOKENS = {
        "descripcion", "description", "observacion", "comentario", "nota", "detalle",
    }
    # Palabras que aparecen en casi cualquier pregunta y no prueban que la
    # sugerencia este anclada al esquema real.
    _GENERIC_TERMS = {
        "datos", "tabla", "registro", "registros", "total", "suma", "promedio",
        "cantidad", "cada", "segun", "todos", "todas", "principal",
        "principales", "mayor", "mayores", "menor", "menores",
    }

    @classmethod
    async def get_dynamic_suggestions_with_llm(
        cls,
        user_role: str,
        allowed_tables: Set[str],
        schema_prompt: str = "",
        table_columns: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        table_labels: Optional[Dict[str, str]] = None,
    ) -> List[str]:
        if not allowed_tables:
            return [
                "¿Qué información puedo consultar con mi perfil?",
                "¿Cómo solicito acceso a tablas adicionales de la base de datos?"
            ]

        known_terms = cls._known_terms(table_columns)

        try:
            system_prompt = PromptManager.get_suggestions_system_prompt()
            tables_str = ", ".join(sorted(allowed_tables))
            prompt_llm = f"""Perfil del usuario: {user_role}
Base de datos activa ({tables_str}):

{schema_prompt}

Genera 4 sugerencias simples y breves de preguntas sobre ESTA base de datos activa."""

            llm_text = await LLMService.generate_completion(
                prompt_llm,
                system_prompt=system_prompt,
                temperature=0.3,
                max_tokens=150
            )

            if llm_text:
                lines = [line.strip() for line in llm_text.split('\n') if line.strip()]
                clean_suggestions = []
                seen = set()
                for line in lines:
                    line_clean = re.sub(r'^[\d\.\-\*\•\>\s]+', '', line).strip()
                    line_clean = line_clean.strip('"\'')
                    if not len(line_clean) > 8:
                        continue
                    if not ('?' in line_clean or '¿' in line_clean):
                        continue
                    if not any(k in line_clean.lower() for k in [
                        'cuál', 'cuáles', 'cuanto', 'cuánto', 'cuantos', 'cuántos',
                        'mostrar', 'total', 'ventas', 'resumen', 'promedio',
                        'ingreso', 'costo', 'listar',
                    ]):
                        continue
                    # Anclaje al esquema: si la metadata esta disponible, una
                    # sugerencia que no nombra ninguna tabla/columna/etiqueta real
                    # es una invencion del modelo y no se muestra. Sin metadata
                    # (tests unitarios o introspeccion caida) no se filtra.
                    if known_terms and not cls._is_grounded(line_clean, known_terms):
                        continue
                    normalized = line_clean.lower()
                    if normalized in seen:
                        continue
                    seen.add(normalized)
                    clean_suggestions.append(line_clean)
                if len(clean_suggestions) >= 2:
                    return clean_suggestions[:4]
        except Exception:
            pass

        return cls.get_dynamic_suggestions(
            user_role,
            allowed_tables,
            schema_prompt=schema_prompt,
            table_columns=table_columns,
            table_labels=table_labels,
        )

    @classmethod
    def get_dynamic_suggestions(
        cls,
        user_role: str,
        allowed_tables: Set[str],
        schema_prompt: str = "",
        table_columns: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        table_labels: Optional[Dict[str, str]] = None,
    ) -> List[str]:
        if not allowed_tables:
            return [
                "¿Qué información puedo consultar con mi perfil?",
                "¿Cómo solicito acceso a tablas adicionales de la base de datos?"
            ]

        # Camino preferido: las sugerencias se derivan de la MISMA metadata que
        # usa el Smart Builder. Son deterministicas, instantaneas y cada una
        # nombra columnas que existen; el LLM queda como enriquecimiento, no como
        # requisito para que la pantalla tenga algo util.
        if table_columns:
            builder_tables = cls.build_builder_options(table_columns, table_labels)
            builder_suggestions = cls._suggestions_from_builder(builder_tables)
            if len(builder_suggestions) >= 2:
                return builder_suggestions[:4]

        suggestions = []
        sorted_tables = sorted(list(allowed_tables), key=lambda t: (0 if t.lower().startswith("fact_") else 1, t.lower()))

        # Extract physical metrics & dimensions from schema_prompt
        #
        # Acepta las DOS formas en que `DynamicSchemaPruningService` renderiza el
        # esquema. La de lista con vinetas (`  - col (TIPO, ej: 'x')`) ya no se
        # emite desde que el prompt va como DDL, pero el fallback tiene que seguir
        # funcionando si alguien guarda un prompt viejo en el cache o cambia el
        # renderizado: el regex viejo solo no matchea NADA y el servicio degrada
        # entero a las plantillas de tabla sin decir por que.
        extracted_metrics = []
        extracted_categories = []
        extracted_dates = []

        if schema_prompt:
            # El DDL no lleva el tipo entre parentesis como la version con
            # vinetas, asi que se captura `nombre TIPO` aHIjo de linea.
            ddl_matches = re.findall(r'^\s*([a-zA-Z0-9_]+)\s+([A-Za-z][A-Za-z0-9_]*)', schema_prompt, re.MULTILINE)
            bullet_matches = re.findall(r'^\s*-\s*([a-zA-Z0-9_]+)\s*\(([^)]+)\)', schema_prompt, re.MULTILINE)

            col_matches = ddl_matches or bullet_matches

            for c_name, c_type in col_matches:
                c_low = c_name.lower()
                t_low = c_type.lower()
                if c_low in {"id", "id_tabla"} or c_low.endswith("_id") or c_low.endswith("_key"):
                    continue
                if any(m in c_low for m in ["monto", "total", "precio", "ingreso", "costo", "venta", "salario", "sueldo", "cantidad", "consumo", "cpu", "ram", "incidente", "unidades", "horas", "duracion"]) or any(it in t_low for it in ["int", "real", "float", "numeric", "decimal", "double"]):
                    extracted_metrics.append(c_name)
                elif any(d in c_low for d in ["fecha", "mes", "anio", "date", "periodo", "created_at", "timestamp"]):
                    extracted_dates.append(c_name)
                elif any(cat in c_low for cat in ["categoria", "producto", "cliente", "departamento", "region", "sucursal", "canal", "tipo", "estado", "ciudad", "pais", "marca", "servidor", "usuario", "proveedor"]):
                    extracted_categories.append(c_name)

        clean_table_names = []
        for tbl in sorted_tables:
            clean_name = tbl
            for prefix in ["fact_", "dim_", "tbl_", "table_"]:
                if clean_name.lower().startswith(prefix):
                    clean_name = clean_name[len(prefix):]
                    break
            clean_table_names.append((tbl, clean_name.replace("_", " ").strip()))

        # If metrics and categories exist
        if extracted_metrics and (extracted_categories or clean_table_names):
            best_metric = extracted_metrics[0].replace("_", " ").strip()
            best_cat = (extracted_categories[0].replace("_", " ").strip()) if extracted_categories else clean_table_names[0][1]
            primary_tbl = clean_table_names[0][1]

            suggestions.append(f"📊 ¿Cuál es el total de {best_metric} agrupado por {best_cat}?")
            suggestions.append(f"📈 Top 5 {best_cat} con mayor {best_metric}")
            if extracted_dates:
                best_date = extracted_dates[0].replace("_", " ").strip()
                suggestions.append(f"📅 Evolución temporal de {best_metric} por {best_date}")
            elif len(extracted_metrics) > 1:
                sec_metric = extracted_metrics[1].replace("_", " ").strip()
                suggestions.append(f"⚡ Comparativa de {best_metric} y {sec_metric} en {primary_tbl}")
            else:
                suggestions.append(f"🔍 Promedio y registros destacados de {best_metric} en {primary_tbl}")

            if len(clean_table_names) > 1:
                sec_tbl = clean_table_names[1][1]
                suggestions.append(f"💡 Resumen consolidado y principales indicadores de {sec_tbl}")
            else:
                suggestions.append(f"📋 Desglose detallado y análisis de {primary_tbl}")

        # Fallback table-based templates
        if len(suggestions) < 4:
            templates = [
                "📊 Distribución y resumen de registros en {name}",
                "📈 Métricas acumuladas y evolución en {name}",
                "📋 Listado detallado y consulta de {name}",
                "💡 Indicadores clave y registros principales de {name}"
            ]

            for i, (orig_tbl, clean_spaced) in enumerate(clean_table_names):
                tmpl = templates[i % len(templates)]
                suggestions.append(tmpl.format(name=clean_spaced))

                if len(clean_table_names) == 1:
                    suggestions.append(f"🔍 Top registros con mayores valores en {clean_spaced}")
                    suggestions.append(f"⚡ Totales agregados y promedio general de {clean_spaced}")

        seen = set()
        unique = []
        for s in suggestions:
            if s not in seen:
                seen.add(s)
                unique.append(s)

        return unique[:4]

    @classmethod
    def build_builder_options(
        cls,
        table_columns: Optional[Dict[str, List[Dict[str, Any]]]],
        table_labels: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        """Convierte la metadata fisica del esquema en opciones para el builder.

        Devuelve solo tablas con al menos una columna util. Las columnas MASKED
        no se ofrecen: agregar o agrupar un valor enmascarado no es una pregunta
        de negocio. Las BLOCKED ni siquiera llegan aca (`dynamic_schema` las omite).
        """
        if not table_columns:
            return []

        labels = table_labels or {}
        ordered_tables = sorted(
            table_columns.items(),
            key=lambda item: (0 if item[0].lower().startswith("fact_") else 1, item[0].lower()),
        )

        builder_tables: List[Dict[str, Any]] = []
        for table_name, columns in ordered_tables:
            metrics: List[Dict[str, Any]] = []
            dimensions: List[Dict[str, str]] = []
            dates: List[Dict[str, str]] = []

            for column in columns or []:
                name = str(column.get("name") or "").strip()
                if not name or column.get("is_masked"):
                    continue
                label = str(column.get("label") or name).strip()
                kind = cls._classify_column(name, column.get("type"))
                if kind == "metric":
                    metrics.append({"column": name, "label": label, "aggregate": "sum"})
                elif kind == "date":
                    dates.append({
                        "name": name,
                        "label": label,
                        "type": str(column.get("type") or ""),
                    })
                elif kind == "dimension":
                    dimensions.append({"name": name, "label": label})

            if not (metrics or dimensions or dates):
                continue

            # Monto/ingreso/venta primero: es la metrica que el usuario espera
            # ver como opcion por defecto, no el primer entero del esquema.
            business_priority = ("monto", "total", "importe", "ingreso", "venta", "valor")
            metrics.sort(key=lambda item: (
                0 if any(token in item["column"].lower() for token in business_priority) else 1,
                item["column"].lower(),
            ))

            count_metric = {"column": None, "label": "Cantidad de registros", "aggregate": "count"}
            builder_tables.append({
                "name": table_name,
                "label": str(labels.get(table_name) or table_name),
                "metrics": (metrics[:5] + [count_metric]),
                "dimensions": dimensions[:8],
                "dates": dates[:2],
            })

        return builder_tables[:12]

    @classmethod
    def _suggestions_from_builder(cls, tables: List[Dict[str, Any]]) -> List[str]:
        suggestions: List[str] = []

        def add(text: str) -> None:
            if text and text not in suggestions:
                suggestions.append(text)

        for table in tables:
            sum_metrics = [m for m in table.get("metrics", []) if m.get("aggregate") == "sum"]
            metric = sum_metrics[0] if sum_metrics else None
            dims = table.get("dimensions", [])
            dates = table.get("dates", [])
            mlabel = metric["label"] if metric else None
            tlabel = table.get("label") or table.get("name")

            if mlabel and dims:
                add(f"📊 ¿Cuál es la suma de {mlabel} por {dims[0]['label']}?")
            if mlabel and dates:
                add(f"📅 ¿Cómo evoluciona {mlabel} por {dates[0]['label']}?")
            if mlabel and len(dims) > 1:
                add(f"🏆 ¿Cuáles son los 5 {dims[1]['label']} con mayor {mlabel}?")
            elif mlabel and dims:
                add(f"🏆 ¿Cuáles son los 5 {dims[0]['label']} con mayor {mlabel}?")
            if len(dims) > 2:
                add(f"📋 ¿Cuántos registros hay por {dims[2]['label']}?")
            elif dims:
                add(f"📋 ¿Cuántos registros hay por {dims[0]['label']}?")
            elif dates:
                add(f"📅 ¿Cuántos registros hay por {dates[0]['label']}?")
            elif mlabel:
                add(f"🔍 ¿Cuál es el promedio de {mlabel}?")
            else:
                add(f"💡 ¿Qué información contiene {tlabel}?")

            if len(suggestions) >= 4:
                break

        return suggestions[:4]

    @classmethod
    def _classify_column(cls, name: str, type_name: Any) -> str:
        tokens = cls._name_tokens(name)
        lower_type = str(type_name or "").lower()

        if cls._is_identifier_column(name):
            return "identifier"
        if (
            any(token in lower_type for token in cls._DATE_TYPE_TOKENS)
            or any(token in cls._DATE_NAME_TOKENS for token in tokens)
        ):
            return "date"
        if (
            any(token in lower_type for token in cls._NUMERIC_TYPE_TOKENS)
            and not any(token in cls._DATE_NAME_TOKENS for token in tokens)
        ):
            return "metric"
        if any(token in cls._FREE_TEXT_TOKENS for token in tokens):
            return "free_text"
        return "dimension"

    @classmethod
    def _is_identifier_column(cls, name: str) -> bool:
        tokens = cls._name_tokens(name)
        if not tokens:
            return False
        if tokens[0] in cls._IDENTIFIER_PREFIXES:
            return True
        return any(token in cls._IDENTIFIER_TOKENS for token in tokens)

    @staticmethod
    def _name_tokens(name: str) -> List[str]:
        return [
            token for token in re.split(r"[^a-z0-9áéíóúñ]+", str(name or "").lower())
            if token
        ]

    @classmethod
    def _known_terms(cls, table_columns: Optional[Dict[str, List[Dict[str, Any]]]]) -> Set[str]:
        terms: Set[str] = set()
        for table_name, columns in (table_columns or {}).items():
            for value in [table_name, *str(table_name).split("_")]:
                if len(value) >= 4:
                    terms.add(value.lower())
            for column in columns or []:
                values = [
                    column.get("name") or "",
                    column.get("label") or "",
                ]
                for value in values:
                    for token in cls._name_tokens(value):
                        if len(token) >= 4 and token not in cls._GENERIC_TERMS:
                            terms.add(token)
        return terms

    @classmethod
    def _is_grounded(cls, suggestion: str, known_terms: Set[str]) -> bool:
        tokens = [
            token for token in cls._name_tokens(suggestion)
            if len(token) >= 4 and token not in cls._GENERIC_TERMS
        ]
        return any(
            token == term
            or (len(token) >= 5 and len(term) >= 5 and (
                token.startswith(term) or term.startswith(token)
            ))
            for token in tokens
            for term in known_terms
        )

import re
import json
import time
import asyncio
from typing import Any, List, Optional, Dict, Tuple
from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.modules.admin_catalog.models import SemanticCatalog, CorporateConnection
from app.modules.admin_catalog.schemas import AutoEnrichRequest, AutoEnrichResponse
from app.modules.chat_engine.llm_service import LLMService, resolve_user_llm_config
from app.modules.system.health_service import HealthService
from app.core.config import settings
from app.core.logging import logger
from app.modules.catalog.services.schema_inspector import SchemaInspector

# --- Limites del enriquecimiento con IA ---------------------------------------
# El enricher original hacia UNA llamada al LLM POR COLUMNA, sin cota, y colgo
# el wizard de ingreso 30+ minutos en inferencia CPU (commit c4cb6bc, que lo
# reemplazo por heuristicas y dejo la opcion "IA" sin IA). Estos son los fences
# que hacen viable la version real: UNA llamada por LOTE de columnas, techo por
# llamada, presupuesto total, corte temprano tras fallos consecutivos, y
# heuristica como baseline de lo que no llegue.
#
# El lote (y no la tabla entera) es deliberado: un JSON de 15 columnas (~1000
# tokens) en CPU supera el techo por llamada o se trunca por `max_tokens`, y en
# ambos casos la tabla PIERDE toda la IA por una sola llamada que no termino
# (caso real del wizard con un 7B Q4 en CPU). Con lotes chicos cada llamada
# termina, y un lote que falla no condena a los demas de la misma tabla.
#
# Son knobs de hardware (viven en `settings`): en una maquina con GPU se pueden
# subir sin tocar el resto de la logica. El tamano efectivo del lote baja solo
# con la velocidad medida del modelo (ver `_columns_per_call`).
LLM_TABLE_TIMEOUT_S = settings.LLM_ENRICH_TABLE_TIMEOUT_S
LLM_TOTAL_BUDGET_S = settings.LLM_ENRICH_TOTAL_BUDGET_S
LLM_COLUMNS_PER_CALL = settings.LLM_ENRICH_COLUMNS_PER_CALL
LLM_MAX_CONSECUTIVE_FAILURES = 2
LLM_TOKENS_PER_COLUMN = 90
LLM_MAX_TOKENS_CAP = 2048

LLM_SYSTEM_PROMPT = (
    "Eres un especialista en gobernanza de datos, catalogos semanticos y ERPs "
    "corporativos (SAP, Oracle, AS/400). Reconoce y traduce con precision "
    "acronimos tecnicos y codigos alemanes de SAP (ej. BKPF, BSEG, BUKRS, WRBTR, "
    "MATNR, KUNNR, VBELN). Responde UNICAMENTE con un JSON valido, sin texto adicional."
)

class CatalogEnricher:
    """
    Encapsulates heuristic inference and LLM-powered semantic enrichment
    for databases and columns.
    """

    SAP_COMMON_MAP = {
        "bukrs": ("Sociedad / Empresa", "Código de sociedad contable independiente (Company Code) en SAP.", "Dimensión de agrupación"),
        "belnr": ("Número de Documento Contable", "Número único de documento o asiento contable en SAP.", "Identificador de transacción"),
        "gjahr": ("Ejercicio Fiscal", "Año o ejercicio contable en el que se registra la transacción.", "DATE_PART('year', fecha)"),
        "wrbtr": ("Importe en Moneda de Documento", "Monto registrado en la moneda original de la transacción.", "SUM(wrbtr)"),
        "dmbtr": ("Importe en Moneda Local", "Monto convertido a la moneda local de la sociedad.", "SUM(dmbtr)"),
        "shkzg": ("Indicador Debe/Haber", "Indica si la posición contable es Débito/Debe ('S') o Crédito/Haber ('H').", "Filtro contable S/H"),
        "matnr": ("Código de Material", "Identificador alfanumérico único del producto o material en SAP.", "Clave foránea (MARA)"),
        "kunnr": ("Código de Cliente", "Identificador único de la cuenta de cliente en SAP.", "Clave foránea (KNA1)"),
        "lifnr": ("Código de Proveedor", "Identificador único de la cuenta de proveedor en SAP.", "Clave foránea (LFA1)"),
        "werks": ("Centro / Planta", "Unidad organizativa de producción, almacenamiento o distribución en SAP.", "Dimensión de centro"),
        "lgort": ("Almacén", "Ubicación física de almacenamiento de mercancías en SAP.", "Dimensión de almacén"),
        "vbeln": ("Documento Comercial / Factura", "Número de documento de ventas, entrega o facturación en SAP.", "Clave de documento comercial"),
        "posnr": ("Posición de Documento", "Número de renglón o ítem dentro del documento comercial o contable.", "Número de ítem"),
        "ebeln": ("Documento de Compra / Pedido", "Número de pedido de compras en SAP.", "Clave de orden de compra"),
        "ebelp": ("Posición de Pedido de Compra", "Número de posición dentro del pedido de compras en SAP.", "Número de ítem"),
        "netwr": ("Valor Neto", "Importe neto del pedido o factura sin impuestos.", "SUM(netwr)"),
        "menge": ("Cantidad", "Cantidad física de unidades de material o servicio.", "SUM(menge)"),
        "meins": ("Unidad de Medida Base", "Unidad de medida del stock o servicio (ej. KG, UN, L).", "Unidad de medida"),
        "bldat": ("Fecha de Documento", "Fecha de emisión física o legal del documento mercantil.", "DATE(bldat)"),
        "budat": ("Fecha de Contabilización", "Fecha en que se registra contablemente el impacto en el libro mayor.", "DATE(budat)"),
        "blart": ("Clase de Documento", "Tipo o clasificación contable del asiento (ej. KR, KZ, SA, DR).", "Dimensión de documento"),
        "waers": ("Moneda de Transacción", "Clave de moneda de la operación (ej. USD, EUR, CLP, MXN).", "Código ISO de moneda"),
        "mwskz": ("Indicador de Impuestos", "Código que define la alícuota o tratamiento tributario (IVA/Tax).", "Clasificación tributaria"),
        "kostl": ("Centro de Coste", "Unidad organizativa responsable del devengo de costos operativos.", "Dimensión de Controlling"),
        "prctr": ("Centro de Beneficio", "Unidad organizativa responsable de la rentabilidad y resultados.", "Dimensión de Controlling"),
        "aufnr": ("Orden de Fabricación / Trabajo", "Número de orden interna o de producción en SAP.", "Identificador de orden"),
    }

    @classmethod
    def heuristic_enrich(cls, table_name: str, col_name: str, col_type: str, samples: List[str]) -> Dict[str, str]:
        t_lower = table_name.lower()
        c_lower = col_name.lower()

        # Direct SAP acronym match
        if c_lower in cls.SAP_COMMON_MAP:
            f_name, d_text, f_formula = cls.SAP_COMMON_MAP[c_lower]
            return {
                "friendly_name": f_name,
                "description": d_text,
                "business_formula": f_formula
            }

        friendly = c_lower.replace("_", " ").title()
        desc = f"Campo '{col_name}' de la tabla {table_name}"
        formula = "Columna directa"

        if c_lower in ("id", "id_" + t_lower, t_lower + "_id", "uuid", "key") or c_lower.startswith("id_") or c_lower.endswith("_id") or c_lower.startswith("cod_") or c_lower.startswith("codigo_"):
            friendly = f"Identificador ({friendly})"
            desc = f"Clave identificadora o código único del registro en {table_name}."
            formula = "Clave Primaria / Foránea (ID)"
        elif "precio" in c_lower or "price" in c_lower:
            friendly = "Precio Unitario"
            desc = f"Valor monetario unitario asignado al elemento en {table_name} en moneda local/USD."
            formula = "ROUND(AVG(precio), 2)"
        elif "monto" in c_lower or "total" in c_lower or "amount" in c_lower or "subtotal" in c_lower:
            friendly = "Monto Total"
            desc = f"Importe financiero o suma acumulada calculada para la transacción en {table_name}."
            formula = f"SUM({col_name})"
        elif "saldo" in c_lower or "balance" in c_lower:
            friendly = "Saldo Financiero"
            desc = f"Saldo remanente o balance monetario en {table_name}."
            formula = f"SUM({col_name})"
        elif "ingreso" in c_lower or "revenue" in c_lower or "venta" in c_lower or "sales" in c_lower:
            friendly = "Ingreso Corporativo"
            desc = f"Total de ingresos o ventas devengadas registradas en {table_name}."
            formula = f"SUM({col_name})"
        elif "costo" in c_lower or "cost" in c_lower or "gasto" in c_lower or "expense" in c_lower:
            friendly = "Costo Operativo"
            desc = f"Costos directos o gastos operativos incurridos en {table_name}."
            formula = f"SUM({col_name})"
        elif "utilidad" in c_lower or "profit" in c_lower or "margen" in c_lower or "margin" in c_lower:
            friendly = "Margen de Utilidad"
            desc = f"Margen o beneficio financiero calculado en {table_name}."
            formula = "ingreso - costo"
        elif "salario" in c_lower or "salary" in c_lower or "sueldo" in c_lower:
            friendly = "Salario / Remuneración"
            desc = f"Compensación monetaria asignada al colaborador en {table_name}."
            formula = f"AVG({col_name})"
        elif "cantidad" in c_lower or "cant" in c_lower or "qty" in c_lower or "quantity" in c_lower or "stock" in c_lower or "unidades" in c_lower:
            friendly = "Cantidad / Volumen"
            desc = f"Volumen físico o unidades cuantitativas registradas en {table_name}."
            formula = f"SUM({col_name})"
        elif "fecha" in c_lower or "date" in c_lower or "timestamp" in c_lower or "dia" in c_lower or "mes" in c_lower or "anio" in c_lower or "año" in c_lower:
            friendly = "Fecha de Registro"
            desc = f"Marca temporal o fecha calendario del evento o transacción en {table_name}."
            formula = f"DATE({col_name})"
        elif "cliente" in c_lower or "customer" in c_lower or "rut" in c_lower:
            friendly = "Cliente / Cuenta"
            desc = f"Entidad o receptor comercial asociado al registro en {table_name}."
            formula = "Dimensión de cliente"
        elif "producto" in c_lower or "product" in c_lower or "articulo" in c_lower or "sku" in c_lower:
            friendly = "Producto / Ítem"
            desc = f"Bien, artículo o servicio referenciado en {table_name}."
            formula = "Dimensión de producto"
        elif "proveedor" in c_lower or "supplier" in c_lower or "vendor" in c_lower:
            friendly = "Proveedor Comercial"
            desc = f"Proveedor de insumos o servicios en {table_name}."
            formula = "Dimensión de proveedor"
        elif "categoria" in c_lower or "category" in c_lower or "segmento" in c_lower or "segment" in c_lower or "rubro" in c_lower:
            friendly = "Categoría / Segmento"
            desc = f"Segmento o clasificación temática para agrupar en {table_name}."
            formula = "Dimensión de agrupación"
        elif "estado" in c_lower or "status" in c_lower or "activo" in c_lower:
            friendly = "Estado del Registro"
            desc = f"Condición o fase en el ciclo de vida del registro en {table_name}."
            formula = "Dimensión de estado"
        elif "tipo" in c_lower or "type" in c_lower:
            friendly = "Tipo / Clasificación"
            desc = f"Tipología o naturaleza operativa en {table_name}."
            formula = "Dimensión de agrupación"
        elif "sucursal" in c_lower or "tienda" in c_lower or "branch" in c_lower or "store" in c_lower:
            friendly = "Sucursal / Tienda"
            desc = f"Punto físico o sucursal comercial en {table_name}."
            formula = "Dimensión geográfica"
        elif "ciudad" in c_lower or "city" in c_lower or "pais" in c_lower or "country" in c_lower:
            friendly = "Ubicación Geográfica"
            desc = f"Localización territorial asociada al registro en {table_name}."
            formula = "Dimensión geográfica"
        elif "nombre" in c_lower or "name" in c_lower or "razon_social" in c_lower or "titulo" in c_lower:
            friendly = f"Nombre de {table_name}"
            desc = f"Denominación comercial o nombre descriptivo en {table_name}."
            formula = "Texto literal"
        elif "descuento" in c_lower or "discount" in c_lower:
            friendly = "Descuento Comercial"
            desc = f"Rebaja o descuento aplicado sobre el monto en {table_name}."
            formula = f"SUM({col_name})"
        elif "impuesto" in c_lower or "tax" in c_lower or "iva" in c_lower:
            friendly = "Impuesto Fiscal"
            desc = f"Monto impositivo o gravamen tributario en {table_name}."
            formula = f"SUM({col_name})"
        elif samples:
            # NO se incluyen valores de muestra en la descripcion. Este texto se
            # persiste en SemanticCatalog.description y sale por dos rutas que el
            # enmascarado de sample_values no cubre: el endpoint de diccionario de
            # datos (que devuelve `description` crudo) y el schema prompt que se
            # manda al LLM. Los valores en claro de columnas MASKED se escapaban por
            # ahi aunque `sample_values` estuviera enmascarado.
            desc = f"Registro tipo {col_type} en {table_name}."

        return {
            "friendly_name": friendly,
            "description": desc,
            "business_formula": formula
        }

    @classmethod
    def _load_existing_index(cls, db: Session, connection_id: int):
        """
        Carga el catalogo de UNA conexion una vez y lo indexa en memoria.

        Existia porque `_find_existing_item` se llamaba una vez por columna, con
        `func.lower(...)` en ambos lados: eso no es N+1 de ORM, es N+1 de red,
        y ademas con predicados que ningun indice puede usar. Con 50 columnas
        eran 50 round-trips para un SELECT que cabe en uno. Este indice es la
        misma consulta con la fila completa en memoria.

        Devuelve dos mapas porque la regla de `schema_name` de
        `_find_existing_item` tiene dos ramas: match exacto cuando se conoce el
        esquema, y match de "esquema desconocido" cuando no o cuando la fila
        existente lo tiene vacio.
        """
        rows = db.query(SemanticCatalog).filter(
            SemanticCatalog.connection_id == connection_id
        ).all()
        by_key: Dict[Tuple[str, str, str], Any] = {}
        by_table_col: Dict[Tuple[str, str], List[Any]] = {}
        for r in rows:
            schema_key = (r.schema_name or "").strip().lower()
            tc = ((r.table_name or "").lower(), (r.column_name or "").lower())
            by_key[tc + (schema_key,)] = r
            by_table_col.setdefault(tc, []).append(r)
        return by_key, by_table_col

    @classmethod
    def _lookup_existing(
        cls,
        index,
        table_name: str,
        column_name: str,
        schema_name: Optional[str]
    ):
        """Equivalente en memoria de `_find_existing_item`, contra el indice precargado."""
        by_key, by_table_col = index
        tc = (table_name.lower(), column_name.lower())
        if schema_name:
            # Se prefiere el match EXACTO sobre el de "esquema desconocido". El
            # `OR` de la version SQL podia devolver cualquiera de los dos y
            # `.first()` sin ORDER BY no garantiza cual; elegir el exacto hace
            # el resultado determinista sin cambiar el conjunto de candidatos.
            return by_key.get(tc + (schema_name.lower(),)) or by_key.get(tc + ("",))
        candidatos = by_table_col.get(tc)
        return candidatos[0] if candidatos else None

    @classmethod
    def _register_new(
        cls,
        index,
        entry: SemanticCatalog
    ) -> None:
        """
        mete una fila recien creada en el indice.

        `db.add()` no la escribe todavia (el commit es del final del metodo), asi
        que sin esto una segunda pasada sobre la misma columna no la encontraria
        e insertaria un duplicado, que revienta el
        `UniqueConstraint("connection_id", "schema_name", "table_name", "column_name")`.
        """
        by_key, by_table_col = index
        schema_key = (entry.schema_name or "").strip().lower()
        tc = ((entry.table_name or "").lower(), (entry.column_name or "").lower())
        by_key[tc + (schema_key,)] = entry
        by_table_col.setdefault(tc, []).append(entry)

    @classmethod
    def seed_catalog_heuristics_for_connection(
        cls,
        db: Session,
        connection_id: int,
        only_tables: Optional[List[str]] = None
    ) -> int:
        """
        Inspects the physical database and automatically generates initial heuristic semantic catalog
        entries for all tables and columns that don't already have catalog entries for this connection.
        """
        conn_obj = db.query(CorporateConnection).filter(CorporateConnection.id == connection_id).first()
        try:
            tables_meta = SchemaInspector.introspect_connection_metadata(conn_obj)
        except Exception as ex:
            # `SchemaInspector` propaga `SchemaIntrospectionError` en vez de devolver
            # [] (no se puede leer != base vacia). Esta siembra es best-effort y sus
            # 3 llamadores (init_db, alta de conector x2) YA persistieron la fila de
            # la conexion, asi que propagar aca daria un 500 por una conexion que si
            # se creo. Mismo criterio que `auto_enrich_catalog`: no se traga en
            # silencio, queda registrado por conexion y no se afirma que se enriquecio.
            logger.error(
                "No se pudo leer la metadata de la conexion %s (%s) para sembrar el catalogo: %s",
                connection_id, getattr(conn_obj, "name", "?"), ex,
            )
            return 0
        if not tables_meta:
            return 0

        if only_tables:
            only_lower = {t.lower() for t in only_tables}
            tables_meta = [t for t in tables_meta if t["table_name"].lower() in only_lower]

        seeded_count = 0
        existing_index = cls._load_existing_index(db, connection_id)
        for tbl_info in tables_meta:
            tbl = tbl_info["table_name"]
            schema_name = tbl_info["schema_name"]
            for col in tbl_info["columns"]:
                col_name = col["name"]
                col_type = col["data_type"]
                sample_vals = col["sample_values"]

                existing = cls._lookup_existing(existing_index, tbl, col_name, schema_name)

                if not existing:
                    meta = cls.heuristic_enrich(tbl, col_name, col_type, sample_vals)
                    new_cat = SemanticCatalog(
                        connection_id=connection_id,
                        schema_name=schema_name,
                        table_name=tbl,
                        column_name=col_name,
                        friendly_name=meta["friendly_name"],
                        description=meta["description"],
                        business_formula=meta["business_formula"],
                        is_ai_generated=True
                    )
                    db.add(new_cat)
                    cls._register_new(existing_index, new_cat)
                    seeded_count += 1

        if seeded_count > 0:
            db.commit()

        return seeded_count

    @staticmethod
    def _build_table_prompt(table_name: str, columns: List[Dict[str, Any]]) -> str:
        """Prompt batch de UNA tabla: todas sus columnas en un solo JSON array.

        Los `sample_values` NO se incluyen a proposito: este texto persiste en
        `SemanticCatalog.description` y sale por dos rutas que el enmascarado no
        cubre (el diccionario de datos y el schema prompt del chat). Mandarlos al
        modelo era la via por la que valores de columnas MASKED se filtraban en
        claro. El admin sigue viendo las muestras en la mesa de revision.
        """
        cols_desc = "; ".join(f"{c['name']} | {c.get('data_type', '')}" for c in columns)
        return (
            f"Tabla: '{table_name}'. Columnas (nombre | tipo): {cols_desc}.\n"
            "Genera para CADA una de las columnas listadas una entrada con "
            "'friendly_name' (nombre de negocio en espanol), 'description' "
            "(descripcion funcional clara) y 'business_formula' (formula o regla "
            "de calculo sugerida).\n"
            "Responde UNICAMENTE con un JSON array, sin texto adicional, con la forma: "
            '[{"column": "<nombre exacto>", "friendly_name": "...", '
            '"description": "...", "business_formula": "..."}]'
        )

    @staticmethod
    def _parse_table_entries(raw: Optional[str]) -> Dict[str, Dict[str, str]]:
        """Extrae el JSON array de la respuesta del modelo, indexado por columna.

        El modelo suele envolver el JSON en texto; se busca el primer array. Una
        respuesta que no parsea devuelve {} y la tabla entera cae a heuristica:
        no hay field-by-field para una respuesta rota.
        """
        if not raw:
            return {}
        match = re.search(r"\[[\s\S]*\]", raw)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
        except Exception:
            return {}
        if not isinstance(data, list):
            return {}
        entries: Dict[str, Dict[str, str]] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            col = str(item.get("column", "") or "").strip().lower()
            if not col:
                continue
            entries[col] = {
                "friendly_name": str(item.get("friendly_name", "") or "").strip(),
                "description": str(item.get("description", "") or "").strip(),
                "business_formula": str(item.get("business_formula", "") or "").strip(),
            }
        return entries

    @classmethod
    def _llm_replacement_allowed(cls, current: Optional[str], heuristic_value: Optional[str]) -> bool:
        """Puede la IA pisar este campo?

        Si (vacio, o el texto actual ES exactamente el placeholder que produce
        `heuristic_enrich`). Un texto escrito a mano no coincide con la plantilla
        y sobrevive, aunque `is_ai_generated` diga True: ese flag no distingue
        humano de maquina (el seeder lo marca siempre), y tratarlo como permiso
        para sobreescribir borraba trabajo curado (bug que cubre
        test_admin_curated_description_is_not_overwritten).

        Techo conocido: si las plantillas heuristicas cambian de redaccion en el
        futuro, los placeholders viejos dejan de matchear y se protegen como si
        fueran curados. Preferible (no se pisa nada) a lo inverso.
        """
        current_text = (current or "").strip()
        if not current_text:
            return True
        return current_text == (heuristic_value or "").strip()

    @staticmethod
    def _chunk_columns(columns: List[Dict[str, Any]], chunk_size: int) -> List[List[Dict[str, Any]]]:
        """Parte las columnas de una tabla en lotes de a lo sumo `chunk_size`."""
        size = max(1, int(chunk_size))
        return [columns[i:i + size] for i in range(0, len(columns), size)]

    @staticmethod
    def _columns_per_call(tokens_per_second: Optional[float]) -> int:
        """Columnas por llamada: las que entren en el techo por llamada.

        Con la velocidad medida (tokens/s, la misma cache que usa la estimacion
        del wizard) se calcula cuantos tokens de salida entran en
        `LLM_TABLE_TIMEOUT_S` con 50% de margen (el modelo puede estar mas
        lento ese dia). Sin medicion, el techo configurado. Minimo 2 columnas:
        de a una llamada multiplicaria los prompts sin necesidad.
        """
        if not tokens_per_second or tokens_per_second <= 0:
            return LLM_COLUMNS_PER_CALL
        affordable = int((LLM_TABLE_TIMEOUT_S * tokens_per_second * 0.5) / LLM_TOKENS_PER_COLUMN)
        return max(2, min(LLM_COLUMNS_PER_CALL, affordable))

    @classmethod
    async def _resolve_chunk_size(cls, llm_base_url: Optional[str], llm_model_name: Optional[str]) -> int:
        """Columnas por llamada, ajustadas a la velocidad medida del modelo."""
        # Import local: `enrich_estimator` importa constantes de este modulo.
        from app.modules.catalog.services.enrich_estimator import EnrichEstimator

        try:
            tps = await EnrichEstimator.measure_tokens_per_second(llm_base_url, llm_model_name)
        except Exception:
            tps = None
        return cls._columns_per_call(tps)

    @classmethod
    async def _enrich_columns_with_llm(
        cls,
        existing_index,
        tbl_info: Dict[str, Any],
        columns: List[Dict[str, Any]],
    ) -> Tuple[Optional[bool], int]:
        """Enriquece UN LOTE de columnas de una tabla con UNA llamada al modelo.

        Devuelve `(resultado, aplicados)`:
          - (None, 0)  no habia nada elegible: no vale gastar una llamada.
          - (False, 0) el modelo no respondio o devolvio algo inusable.
          - (True, n)  n columnas recibieron descripcion del modelo.
        """
        tbl = tbl_info["table_name"]
        schema_name = tbl_info["schema_name"]
        if not columns:
            return None, 0

        eligible = False
        for col in columns:
            row = cls._lookup_existing(existing_index, tbl, col["name"], schema_name)
            if row is None:
                continue
            heur = cls.heuristic_enrich(
                tbl, col["name"], col.get("data_type", ""), col.get("sample_values") or []
            )
            for field in ("friendly_name", "description", "business_formula"):
                if cls._llm_replacement_allowed(getattr(row, field, None), heur[field]):
                    eligible = True
                    break
            if eligible:
                break
        if not eligible:
            return None, 0

        prompt = cls._build_table_prompt(tbl, columns)
        max_tokens = min(LLM_MAX_TOKENS_CAP, LLM_TOKENS_PER_COLUMN * len(columns) + 128)
        try:
            raw = await asyncio.wait_for(
                LLMService.generate_completion(
                    prompt,
                    system_prompt=LLM_SYSTEM_PROMPT,
                    max_tokens=max_tokens,
                    temperature=0.2,
                ),
                timeout=LLM_TABLE_TIMEOUT_S,
            )
        except Exception as ex:
            logger.warning("Enriquecimiento IA: la tabla %s no obtuvo respuesta: %s", tbl, ex)
            return False, 0

        entries = cls._parse_table_entries(raw)
        if not entries:
            logger.warning("Enriquecimiento IA: la tabla %s devolvio JSON inusable.", tbl)
            return False, 0

        applied = 0
        for col in columns:
            entry = entries.get(col["name"].strip().lower())
            if not entry:
                continue
            row = cls._lookup_existing(existing_index, tbl, col["name"], schema_name)
            if row is None:
                continue
            heur = cls.heuristic_enrich(
                tbl, col["name"], col.get("data_type", ""), col.get("sample_values") or []
            )
            touched = False
            for field in ("friendly_name", "description", "business_formula"):
                value = entry.get(field) or ""
                if value and cls._llm_replacement_allowed(getattr(row, field, None), heur[field]):
                    setattr(row, field, value)
                    touched = True
            if touched:
                row.is_ai_generated = True
                applied += 1
        return True, applied

    @classmethod
    async def auto_enrich_catalog(cls, db: Session, req: Optional[AutoEnrichRequest] = None) -> AutoEnrichResponse:
        conn_req_id = req.connection_id if req else None

        # Fase IA: opt-in y con puerta de conectividad. Contra un LLM caido, el
        # probe TCP decide en ~1s y evita pagar la cadena completa de timeouts
        # (varios segundos) por tabla antes de rendirse. El probe NO garantiza
        # que una inferencia responda: por eso los fallos por tabla siguen
        # contando y cortando.
        llm_requested = bool(req and req.use_llm)
        llm_base_url: Optional[str] = None
        llm_model_name: Optional[str] = None
        llm_skip_note = ""
        if req and req.use_llm:
            # En central/external devuelve (None, None) y el probe usa la config
            # del servidor; en user valida la URL antes de emitir una request.
            llm_base_url, llm_model_name = resolve_user_llm_config(req.base_url, req.model_name)
            try:
                probe = await HealthService.check_llm_connectivity(
                    base_url=llm_base_url,
                    model_name=llm_model_name,
                    timeout=2.0,
                    trusted=True,
                )
            except Exception as ex:
                probe = {"success": False, "message": str(ex)}
            if not probe.get("success"):
                llm_skip_note = (
                    " El motor LLM configurado no respondió al chequeo de conectividad, "
                    "así que el catálogo quedó con reglas heurísticas."
                )

        llm_enabled = llm_requested and not llm_skip_note

        targets: List[CorporateConnection] = []
        failed_connections: List[str] = []
        if conn_req_id:
            conn_obj = db.query(CorporateConnection).filter(CorporateConnection.id == conn_req_id).first()
            if not conn_obj:
                # Un id explicito que no existe es 404, no un barrido silencioso
                # de cero campos (mismo corte que el diccionario de datos).
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Conexión corporativa {conn_req_id} no encontrada.",
                )
            targets.append(conn_obj)
        else:
            active_conns = db.query(CorporateConnection).filter(CorporateConnection.is_active == True).all()
            if not active_conns:
                active_conns = db.query(CorporateConnection).all()
            targets = list(active_conns)

        if not targets and not failed_connections:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No se encontró una base de datos activa para auto-enriquecer."
            )

        total_enriched_count = 0
        llm_enriched_count = 0
        llm_consecutive_failures = 0
        llm_stopped_early = False
        # Por que se corto la fase: "budget" (se agoto el tiempo) o "failures"
        # (el modelo fallo lotes seguidos). Los mensajes difieren: decir "limite
        # de tiempo" cuando el modelo respondio basura seria mentir.
        llm_cut_reason: Optional[str] = None
        # El presupuesto es del request entero, no por conexion: un barrido de N
        # conexiones no puede multiplicar el techo por N.
        llm_phase_started_at = time.time() if llm_enabled else None
        last_conn_id = targets[0].id if targets else None

        for conn_obj in targets:
            conn_id = conn_obj.id
            last_conn_id = conn_id
            existing_index = cls._load_existing_index(db, conn_id)
            try:
                tables_meta = SchemaInspector.introspect_connection_metadata(conn_obj)
            except Exception as ex:
                # No se puede leer la conexión no es lo mismo que "no hay nada que
                # enriquecer". Antes se tragaba el fallo y respondía 0 campos.
                failed_connections.append(f"{getattr(conn_obj, 'name', conn_id)}: {ex}")
                continue
            if not tables_meta:
                # Una lista vacía tampoco distingue "base sin tablas" de "no se pudo
                # leer". Se reporta como no verificado en vez de como éxito vacío.
                failed_connections.append(
                    f"{getattr(conn_obj, 'name', conn_id)}: no se obtuvo metadata de ninguna tabla (conexión inaccesible o vacía)."
                )
                continue
            if req and req.table_name:
                tables_meta = [t for t in tables_meta if t["table_name"].lower() == req.table_name.lower()]

            for tbl_info in tables_meta:
                tbl = tbl_info["table_name"]
                schema_name = tbl_info["schema_name"]
                for col_info in tbl_info["columns"]:
                    col_name = col_info["name"]
                    col_type = col_info["data_type"]
                    sample_vals = col_info["sample_values"]

                    existing = cls._lookup_existing(existing_index, tbl, col_name, schema_name)

                    # ponytail: Instant heuristic enrichment (<0.001s) replaces the O(N) sequential LLM
                    # call loop that hung the database registration wizard for 30+ minutes on local CPU inference.
                    meta = cls.heuristic_enrich(tbl, col_name, col_type, sample_vals)

                    if existing:
                        # Solo se rellena lo que está vacío. `is_ai_generated` está en
                        # True para TODO lo que siembra el heurístico, así que usarlo
                        # como permiso para sobreescribir Borrava el trabajo de un
                        # administrador que editó description/friendly_name a mano.
                        touched = False
                        for field in ("friendly_name", "description", "business_formula"):
                            if not (getattr(existing, field) or "").strip():
                                setattr(existing, field, meta[field])
                                touched = True
                        if touched:
                            existing.is_ai_generated = True
                            total_enriched_count += 1
                    else:
                        new_cat = SemanticCatalog(
                            connection_id=conn_id,
                            schema_name=schema_name,
                            table_name=tbl,
                            column_name=col_name,
                            friendly_name=meta["friendly_name"],
                            description=meta["description"],
                            business_formula=meta["business_formula"],
                            is_ai_generated=True
                        )
                        db.add(new_cat)
                        cls._register_new(existing_index, new_cat)
                        total_enriched_count += 1

            # --- Fase IA: despues de la heuristica, que es la baseline. --------
            # Lo que el modelo aplique pisa solo placeholders heuristicos (ver
            # `_llm_replacement_allowed`); si nada llega, el catalogo ya quedo
            # usable gracias a la pasada de arriba.
            if llm_enabled:
                with LLMService.request_config(llm_base_url, llm_model_name):
                    chunk_size = await cls._resolve_chunk_size(llm_base_url, llm_model_name)
                    # Unidades de trabajo: (tabla, lote de columnas). El
                    # presupuesto y el corte temprano se evaluan por lote, no
                    # por tabla: un lote que falla no condena a los demas de la
                    # misma tabla.
                    units = [
                        (tbl_info, chunk)
                        for tbl_info in tables_meta
                        for chunk in cls._chunk_columns(tbl_info.get("columns") or [], chunk_size)
                    ]
                    for tbl_info, chunk in units:
                        if time.time() - llm_phase_started_at > LLM_TOTAL_BUDGET_S:
                            llm_stopped_early = True
                            llm_cut_reason = "budget"
                            break
                        if llm_consecutive_failures >= LLM_MAX_CONSECUTIVE_FAILURES:
                            llm_stopped_early = True
                            llm_cut_reason = "failures"
                            break
                        result, applied = await cls._enrich_columns_with_llm(existing_index, tbl_info, chunk)
                        if result is None:
                            # Nada elegible (todo curado o vacio): no es un fallo
                            # del modelo y no debe cortar la fase.
                            continue
                        if result:
                            llm_consecutive_failures = 0
                            llm_enriched_count += applied
                        else:
                            llm_consecutive_failures += 1

        db.commit()

        # Query all items for the target connection(s)
        query = db.query(SemanticCatalog)
        if conn_req_id:
            query = query.filter(SemanticCatalog.connection_id == conn_req_id)
        elif len(targets) == 1:
            query = query.filter(SemanticCatalog.connection_id == last_conn_id)

        all_items = query.order_by(SemanticCatalog.table_name, SemanticCatalog.column_name).all()

        # Un `success=True` con 0 campos enriquecidos afirmaba "ya estaba todo bien"
        # incluso cuando no se pudo leer ni una conexión. El éxito real es: se
        # enrichió algo, o no había nada pendiente y todas las conexiones se leyeron.
        if not failed_connections and total_enriched_count > 0:
            message = (
                f"Catálogo semántico enriquecido exitosamente: {total_enriched_count} "
                "campos procesados y guardados en la base de datos."
            )
            success = True
        elif not failed_connections:
            message = (
                "No se modificó ningún campo: el catálogo ya tenía todo lo enriquecido "
                "o no había columnas que enriquecer en las conexiones indicadas."
            )
            success = True
        elif total_enriched_count > 0:
            message = (
                f"Catálogo semántico enriquecido parcialmente: {total_enriched_count} campos "
                f"procesados. No se pudieron leer {len(failed_connections)} conexión(es): "
                + " | ".join(failed_connections)
            )
            success = True
        else:
            message = (
                "No se enriqueció ningún campo porque no se pudo leer la metadata de las "
                f"conexiones indicadas ({len(failed_connections)} conexión(es)): "
                + " | ".join(failed_connections)
            )
            success = False

        # El resultado de la IA se declara aparte del heurístico: si el modelo no
        # respondió o no llegó, el mensaje lo dice en vez de dejar creer que la
        # descripción que se ve salió del modelo.
        if llm_requested:
            if llm_skip_note:
                message += llm_skip_note
            elif llm_stopped_early and llm_cut_reason == "budget":
                if llm_enriched_count > 0:
                    message += (
                        f" La IA alcanzó a describir {llm_enriched_count} campo(s) antes de "
                        "agotar su límite de tiempo; el resto quedó con reglas heurísticas."
                    )
                else:
                    message += (
                        " La IA no alcanzó a responder dentro del límite de tiempo; el "
                        "catálogo quedó con reglas heurísticas."
                    )
            elif llm_stopped_early:
                # Corte por fallos consecutivos: el modelo no respondio (o
                # respondio inusable) dos lotes seguidos. No es un limite de
                # tiempo y el mensaje no puede decir que si.
                if llm_enriched_count > 0:
                    message += (
                        f" La IA alcanzó a describir {llm_enriched_count} campo(s) antes de que "
                        "el modelo fallara en llamadas consecutivas; el resto quedó con reglas heurísticas."
                    )
                else:
                    message += (
                        " El modelo de IA falló en llamadas consecutivas (sin respuesta o "
                        "respuestas inusables); el catálogo quedó con reglas heurísticas."
                    )
            elif llm_enriched_count > 0:
                message += f" La IA describió {llm_enriched_count} campo(s) con el modelo local."
            else:
                message += (
                    " El modelo de IA no devolvió descripciones usables; el catálogo "
                    "quedó con reglas heurísticas."
                )

        return AutoEnrichResponse(
            success=success,
            message=message,
            enriched_count=total_enriched_count,
            llm_enriched_count=llm_enriched_count,
            llm_stopped_early=llm_stopped_early,
            catalog_items=all_items
        )

"""Vocabulario de respaldo para preguntas en espanol.

No es politica de negocio: es el ultimo recurso cuando el catalogo semantico o
el LLM no alcanzan. Los umbrales y las reglas de producto viven en `policy.py`;
aca solo hay palabras, para que el idioma se revise en un solo lugar.

Si el producto se abre a otro idioma, este modulo se reemplaza por un lexicon
por locale o por sinonimos del catalogo. No se dispersa de nuevo en los modulos.
"""

# Preguntas que piden un conteo y no un importe.
COUNT_WORDS = (
    "cantidad", "cuantos", "cuántos", "registros", "tramites",
    "trámites", "volumen", "conteo", "count",
)

# Preguntas que mezclan "ganancia" con un porcentaje que el motor no aplica.
GAIN_WORDS = (
    "ganancia", "ganancias", "utilidad", "utilidades", "margen",
    "rentabilidad", "profit",
)

# Nombres que, sin catalogo, sugieren una metrica y no un identificador.
METRIC_NAME_TOKENS = (
    "monto", "total", "ingreso", "venta", "importe", "amount", "revenue",
    "value", "valor", "cantidad", "saldo", "precio", "costo", "gasto",
    "utilidad", "margen", "conteo", "count", "suma", "promedio", "peso",
)

# Nombres que identifican una fila o un documento, no una cantidad.
IDENTIFIER_NAME_TOKENS = (
    "folio", "codigo", "cod_", "documento", "clave", "numero",
)

# Cantidades escritas con palabras que el parser de horizonte reconoce.
NUMBER_WORDS = {
    "un": 1, "una": 1, "uno": 1,
    "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12,
}

# Contexto de futuro/historico alrededor de una mencion de horizonte.
FUTURE_MARKERS = (
    "proyect", "pronostic", "estim", "predec", "predic", "adelant",
    "futuro", "proxim", "siguiente", "que viene",
)
HISTORY_MARKERS = ("ultim", "pasado", "anterior", "histor")

# Preguntas que piden un grupo (producto, categoria, canal) y no el total.
DIMENSION_QUESTION_PATTERN = (
    r"(cu[aá]l|cual|qui[eé]n|quien|ranking|top|por producto|por categor[ií]a"
    r"|categor[ií]a|por canal)"
)

# Unidades del parser de horizonte y su granularidad.
UNIT_PATTERN = "dias?|semanas?|meses?|mes|trimestres?|anos?"
UNIT_GRANULARITY = {
    "dia": "day", "dias": "day",
    "semana": "week", "semanas": "week",
    "mes": "month", "meses": "month",
    "trimestre": "quarter", "trimestres": "quarter",
    "ano": "month", "anos": "month",
}
UNIT_FACTOR = {"ano": 12, "anos": 12}

# Pistas de granularidad cuando la pregunta no da una cantidad de periodos.
GRANULARITY_HINTS = (
    (r"\b(semanal(?:es|mente)?|por semana|semana a semana)\b", "week"),
    (r"\b(diari[oa]s?|diariamente|por dia|dia a dia)\b", "day"),
    (r"\b(mensual(?:es|mente)?|por mes|mes a mes)\b", "month"),
    (r"\b(trimestral(?:es|mente)?|por trimestre|trimestre a trimestre)\b", "quarter"),
)

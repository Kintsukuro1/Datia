"""Formato de numeros para la prosa y las razones de Proyecciones.

Una sola implementacion para que la plantilla, el diagnostico y el semaforo no
publiquen "2.6%" en un texto en espanol. No es politica de negocio.
"""


def pct_text(value: float) -> str:
    """Porcentaje en formato espanol: 3.6 -> "3,6%", 15.0 -> "15%"."""
    text = f"{value:.1f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text.replace('.', ',')}%"

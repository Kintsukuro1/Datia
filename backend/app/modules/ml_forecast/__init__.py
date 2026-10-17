"""Proyecciones de series temporales para el modulo de predicciones.

Aca vive la aritmetica de la proyeccion a varios pasos: cuatro metodos de ajuste
(naive, media, tendencia por minimos cuadrados y Holt amortiguado), el backtest
walk-forward que elige cual se publica, y la banda de error por paso.

El modulo es PURO: sin BD, sin LLM, sin I/O. Recibe la serie ya extraida y
devuelve numeros. Lo que no se puede calcular se publica como None, nunca como
un cero que el lector tomaria por un dato.
"""

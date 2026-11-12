"""Una sola politica para proyecciones: limites, umbrales y derivaciones.

Los valores siguen siendo decisiones de producto, pero viven en un solo lugar.
El servicio los publica en la respuesta para que la UI no repita "36" ni "6"
hardcodeados, y las derivaciones salen del largo de la serie y de la
granularidad, no de una tabla fija por caso.

Este modulo no importa nada de `ml_forecast`: solo toma `MIN_PERIODS` del
forecast existente, que es stdlib pura. Asi no hay ciclos de import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from app.modules.chat_engine.forecast_calculator import MIN_PERIODS


@dataclass(frozen=True)
class ProjectionPolicy:
    """Contrato unico de la politica de proyecciones."""

    # Horizontes publicables.
    min_horizon: int = 1
    max_horizon: int = 36
    max_scenario_horizon: int = 120
    default_horizon: int = 6
    default_scenario_growth_pct: float = 0.0
    min_recommended_horizon: int = 3
    horizon_history_divisor: int = 3

    # Historia minima y evidencia.
    min_periods: int = MIN_PERIODS
    min_diagnosable: int = 3
    reliable_backtests: int = 4

    # Umbrales de calidad. Se publican para que la UI no los invente.
    exploratory_mape: float = 30.0
    unreliable_mape: float = 50.0
    low_cv_pct: float = 15.0
    trend_window: int = 24
    trend_threshold_pct: float = 0.5
    trend_min_t: float = 2.0
    seasonality_ratio: float = 1.15
    seasonality_min_eta2: float = 0.15
    outlier_pct: float = 10.0
    outlier_z: float = 3.5
    level_shift_window: int = 3
    level_shift_z: float = 2.5

    # Presentacion.
    breakdown_top_n: int = 5
    max_prompt_columns: int = 120
    scenario_rate_tolerance_pct: float = 0.5
    scenario_growth_min_pct: float = -99.0
    scenario_growth_max_pct: float = 1000.0
    scenario_example_growth_pct: float = 10.0
    scenario_historical_quantile: float = 0.50
    scenario_conservative_quantile: float = 0.25
    scenario_optimistic_quantile: float = 0.75

    # Operacion del LLM y estimacion de duracion. No son reglas de negocio, pero
    # tampoco van dispersas: el techo de tokens y la velocidad asumida cambian
    # juntos con el modelo y el hardware.
    analysis_max_tokens: int = 160
    prompt_base_tokens: float = 400.0
    prompt_speed_factor: float = 4.0
    local_overhead_s: float = 2.0
    measured_floor_s: float = 5.0
    fallback_seconds: float = 60.0
    chars_per_token: float = 4.0

    # Calibracion de intervalos. La banda se elige para cubrir esta fraccion de
    # los residuos walk-forward; con menos de `interval_min_residuals` se cae al
    # peor caso observado en vez de fingir un cuantil.
    interval_coverage: float = 0.80
    interval_coverage_floor: float = 0.50
    interval_coverage_ceiling: float = 0.99
    interval_min_residuals: int = 8
    interval_coverage_tolerance: float = 0.10
    interval_use_worst_case_on_shift: bool = True

    # Resolucion de tabla/columna. Son heuristicas de respaldo para cuando el
    # catalogo semantico no alcanza; viven aca para que el peso y el umbral no
    # queden enterrados en el resolvedor.
    table_score_name_phrase: int = 100
    table_score_friendly_phrase: int = 90
    table_score_name_token: int = 20
    table_score_catalog_token: int = 15
    dimension_table_prefixes: tuple = ("dim", "dwh_dim")
    identifier_ratio: float = 0.95
    numeric_text_ratio: float = 0.95
    code_max_distinct: int = 100
    code_max_ratio: float = 0.01
    min_year: int = 1900
    max_year: int = 2100
    history_default_limit: int = 10
    history_max_limit: int = 20

    # Periodo en curso incompleto.
    partial_period_min_ratio: float = 0.7
    partial_period_closed_ratio: float = 0.9

    def season_length(self, granularity: str) -> int:
        """Ciclo estacional que se puede medir con esta granularidad.

        Diario: dia de la semana. Semanal: mes del ano (12), que necesita 24
        semanas y es alcanzable; 52 semanas exigiria dos anos. Mensual: mes.
        Trimestral: trimestre.
        """
        return {
            "day": 7,
            "week": 12,
            "month": 12,
            "quarter": 4,
        }.get(granularity, 12)

    def seasonality_min_points(self, granularity: str) -> int:
        """Dos ciclos completos: con uno solo no se distingue patron de rareza."""
        return 2 * self.season_length(granularity)

    def steps_per_year(self, granularity: str) -> int:
        """Periodos por ano para convertir una tasa anual a la granularidad."""
        return {
            "day": 365,
            "week": 52,
            "month": 12,
            "quarter": 4,
        }.get(granularity, 12)

    def max_horizon_for(self, n_periods: int) -> int:
        """Pasos que la historia alcanza a sostener, con tope publicable.

        La regla de producto sigue siendo un tercio de la historia observada,
        con piso 3 y techo `max_horizon`. 0 significa "ni el primer paso tiene
        historia": con menos de `min_periods` no se publica un pronostico.
        """
        if n_periods < self.min_periods:
            return 0
        return max(
            self.min_recommended_horizon,
            min(self.max_horizon, n_periods // self.horizon_history_divisor),
        )

    def reliable_horizon_for(self, n_periods: int) -> int:
        """Pasos con al menos `reliable_backtests` residuos medidos.

        El paso j tiene `n - min_periods - (j - 1)` origenes. Para que ese
        numero llegue al piso de evidencia, j no puede superar
        `n - min_periods - reliable_backtests + 1`. Se recorta al horizonte
        recomendado por la historia para no prometer mas de lo que la regla de
        producto ya publica.
        """
        if n_periods < self.min_periods:
            return 0
        evidence = n_periods - self.min_periods - self.reliable_backtests + 1
        return max(0, min(self.max_horizon_for(n_periods), evidence))

    def public(self) -> Dict[str, Any]:
        """Subconjunto estable para el contrato HTTP y la UI."""
        return {
            "min_horizon": self.min_horizon,
            "max_horizon": self.max_horizon,
            "max_scenario_horizon": self.max_scenario_horizon,
            "default_horizon": self.default_horizon,
            "default_scenario_growth_pct": self.default_scenario_growth_pct,
            "min_periods": self.min_periods,
            "min_diagnosable": self.min_diagnosable,
            "reliable_backtests": self.reliable_backtests,
            "min_recommended_horizon": self.min_recommended_horizon,
            "exploratory_mape": self.exploratory_mape,
            "unreliable_mape": self.unreliable_mape,
            "breakdown_top_n": self.breakdown_top_n,
            "scenario_growth_min_pct": self.scenario_growth_min_pct,
            "scenario_growth_max_pct": self.scenario_growth_max_pct,
            "interval_coverage": self.interval_coverage,
            "interval_min_residuals": self.interval_min_residuals,
        }


POLICY = ProjectionPolicy()

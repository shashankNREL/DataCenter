from __future__ import annotations

from pathlib import Path
from typing import Union

import numpy as np
import pandas as pd

from .unit import GasTurbinePlant, _DEFAULT_RATED_POWER_W

ArrayLike = Union[float, np.ndarray, list, tuple]

# Bottoming-cycle defaults (1x1x1 single-shaft CCPP, 3-pressure-level HRSG
# with reheat — matching ThermoPower's CCPP_Sim3 / HRSG_3LRh design point):
#
# - eta_bottoming_nominal: thermal efficiency of the bottoming cycle at the
#   topping cycle's nominal exhaust temperature (843 K for the ThermoPower
#   GasTurbineSimplified). 0.32 is typical for modern 3-pressure-reheat HRSG.
# - T_stack_K: temperature at which flue gas leaves the HRSG stack. 363 K
#   matches CCPP_Sim3's sinkGas(T=362.309) so the heat balance is consistent
#   with what ThermoPower itself computes.
# - cp_gas: combustion-product specific heat at HRSG-average temperature.
#   1100 J/kg/K is the standard value for natural-gas flue gas.
# - T_exh_nominal_K: the GT exhaust temperature at full load that defines
#   eta_bottoming_nominal. 843 K matches ThermoPower's flueGasNomTemp.
_DEFAULT_ETA_BOTTOMING_NOMINAL = 0.32
_DEFAULT_T_STACK_K = 363.0
_DEFAULT_CP_GAS_J_KG_K = 1100.0
_DEFAULT_T_EXH_NOMINAL_K = 843.0

# Rated power for a 1x1 CCPP with 235 MW GT and a typical bottoming-cycle
# uplift. Computed from the default parameters at full load: matches what
# the analytical layer produces, so the larger tool sees a self-consistent
# default size.
_DEFAULT_CCPP_RATED_POWER_W = 338.7e6


class CombinedCyclePlant:
    """Combined-cycle plant surrogate.

    Internally holds a `GasTurbinePlant` and layers an analytical HRSG +
    bottoming-cycle energy balance on top of its outputs. See
    `phase4_ccpp.md` and `memory/project_ccpp_strategy.md` for the
    rationale (ThermoPower 3.1's CCPP_Sim3 has a compile-blocking bug;
    industry practice for dispatch tools is the analytical approach used
    here).

    The class exposes the same `dispatch` / `dispatch_profile` API as
    `GasTurbinePlant` so the two are interchangeable inside a `Fleet`.
    """

    def __init__(
        self,
        rated_power_mw: float = _DEFAULT_CCPP_RATED_POWER_W / 1e6,
        eta_bottoming_nominal: float = _DEFAULT_ETA_BOTTOMING_NOMINAL,
        T_stack_K: float = _DEFAULT_T_STACK_K,
        cp_gas_j_kg_k: float = _DEFAULT_CP_GAS_J_KG_K,
        T_exh_nominal_K: float = _DEFAULT_T_EXH_NOMINAL_K,
        co2_per_fuel_kg: float = 2.75,
        fuel_lhv_j_kg: float = 49e6,
        table_path: Path | None = None,
    ):
        self.rated_power_mw = float(rated_power_mw)
        self.eta_bottoming_nominal = float(eta_bottoming_nominal)
        self.T_stack_K = float(T_stack_K)
        self.cp_gas_j_kg_k = float(cp_gas_j_kg_k)
        self.T_exh_nominal_K = float(T_exh_nominal_K)
        self.co2_per_fuel_kg = float(co2_per_fuel_kg)
        self.fuel_lhv_j_kg = float(fuel_lhv_j_kg)

        # Underlying GT runs at its native ThermoPower rated power. The
        # CCPP-level rated_power_mw is just a label / scaling target;
        # internally we always go through the 235 MW GT surrogate and
        # then rescale the *combined* output to match rated_power_mw.
        self._gt = GasTurbinePlant(
            rated_power_mw=_DEFAULT_RATED_POWER_W / 1e6,
            co2_per_fuel_kg=co2_per_fuel_kg,
            fuel_lhv_j_kg=fuel_lhv_j_kg,
            table_path=table_path,
        )
        # Compute the nominal CCPP output that this analytical layer
        # produces from the default GT, then scale all power-side outputs
        # so they aggregate to the requested rated_power_mw at full load.
        nom = self._raw_dispatch(np.array(1.0))
        self._size_scale = (rated_power_mw * 1e6) / float(nom["power_w"])

    def _raw_dispatch(self, load: np.ndarray) -> dict:
        """Apply the HRSG + bottoming-cycle layer to GT outputs (unscaled)."""
        gt = self._gt.dispatch(load)
        # Re-wrap scalar outputs as 0-D arrays for uniform handling
        m_exh = np.asarray(gt["exhaust_m_kg_s"])
        T_exh = np.asarray(gt["exhaust_T_K"])
        p_gt = np.asarray(gt["power_w"])
        fuel = np.asarray(gt["fuel_kg_s"])
        co2 = np.asarray(gt["co2_kg_s"])

        # Heat available to the HRSG (positive when T_exh > T_stack)
        dT_hrsg = np.maximum(T_exh - self.T_stack_K, 0.0)
        Q_HRSG = m_exh * self.cp_gas_j_kg_k * dT_hrsg

        # Carnot-style derate of bottoming efficiency with exhaust temperature
        dT_nom = self.T_exh_nominal_K - self.T_stack_K
        eta_bottoming = self.eta_bottoming_nominal * np.where(
            dT_hrsg > 0, dT_hrsg / dT_nom, 0.0
        )
        p_st = eta_bottoming * Q_HRSG
        p_total = p_gt + p_st

        return {
            "power_w": p_total,
            "fuel_kg_s": fuel,
            "exhaust_m_kg_s": m_exh,
            # Stack temperature is the post-HRSG flue gas temp the plant
            # emits to atmosphere — fixed by HRSG design.
            "exhaust_T_K": np.full_like(np.asarray(T_exh, dtype=float),
                                        self.T_stack_K),
            "co2_kg_s": co2,
            # Carry the intermediate values for diagnostics
            "_p_gt_w": p_gt,
            "_p_st_w": p_st,
            "_eta_bottoming": eta_bottoming,
            "_Q_HRSG_w": Q_HRSG,
        }

    def dispatch(self, load: ArrayLike) -> dict:
        arr = np.asarray(load, dtype=float)
        scalar_input = arr.ndim == 0
        if np.any(np.isnan(arr)):
            raise ValueError("load contains NaN")
        if np.any((arr < 0.0) | (arr > 1.0)):
            raise ValueError(
                f"load must be in [0, 1]; got range [{float(arr.min())}, "
                f"{float(arr.max())}]"
            )

        raw = self._raw_dispatch(arr)
        power_w = raw["power_w"] * self._size_scale
        fuel_kg_s = raw["fuel_kg_s"] * self._size_scale
        exhaust_m_kg_s = raw["exhaust_m_kg_s"] * self._size_scale
        co2_kg_s = raw["co2_kg_s"] * self._size_scale
        exhaust_T_K = raw["exhaust_T_K"]  # stack temp doesn't scale with size

        with np.errstate(divide="ignore", invalid="ignore"):
            denom = fuel_kg_s * self.fuel_lhv_j_kg
            efficiency = np.where(denom > 0, power_w / denom, 0.0)

        out = {
            "power_w": power_w,
            "fuel_kg_s": fuel_kg_s,
            "efficiency": efficiency,
            "exhaust_m_kg_s": exhaust_m_kg_s,
            "exhaust_T_K": exhaust_T_K,
            "co2_kg_s": co2_kg_s,
        }
        if scalar_input:
            return {k: float(np.asarray(v)) for k, v in out.items()}
        return out

    def dispatch_profile(self, load_series: pd.Series) -> pd.DataFrame:
        result = self.dispatch(load_series.to_numpy())
        return pd.DataFrame(result, index=load_series.index)

    def __repr__(self) -> str:
        return f"CombinedCyclePlant(rated_power_mw={self.rated_power_mw:.1f})"

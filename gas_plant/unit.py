from __future__ import annotations

from pathlib import Path
from typing import Union

import numpy as np
import pandas as pd
from scipy.interpolate import interp1d

ArrayLike = Union[float, np.ndarray, list, tuple]

# ThermoPower GasTurbineSimplified defaults — see PowerPlants.mo:140-168
_DEFAULT_RATED_POWER_W = 235e6
_DEFAULT_FUEL_LHV_J_KG = 49e6
# Natural gas (CH4-dominated) stoichiometric CO2 per kg fuel
_DEFAULT_CO2_PER_FUEL_KG = 2.75

# Surrogate CSV is bundled inside the package so the runtime component is
# importable from anywhere with no path assumptions.
_DATA_DIR = Path(__file__).resolve().parent / "data"
_DEFAULT_TABLE_PATH = _DATA_DIR / "gas_turbine_surrogate.csv"


class GasTurbinePlant:
    """Simple-cycle gas turbine surrogate.

    Backed by an offline ThermoPower sweep (see tools/build_surrogate/).
    All calls are pure-Python linear interpolation over the bundled table —
    no Modelica/OpenModelica dependency at runtime.

    Power, fuel flow, and exhaust mass flow scale linearly with
    rated_power_mw relative to the ThermoPower default of 235 MW. Exhaust
    temperature is treated as size-invariant (thermodynamic property of the
    operating point). Efficiency is computed at evaluation time from
    power / (fuel * LHV) so it is independent of size.
    """

    def __init__(
        self,
        rated_power_mw: float = _DEFAULT_RATED_POWER_W / 1e6,
        co2_per_fuel_kg: float = _DEFAULT_CO2_PER_FUEL_KG,
        fuel_lhv_j_kg: float = _DEFAULT_FUEL_LHV_J_KG,
        table_path: Path | None = None,
    ):
        self.rated_power_mw = float(rated_power_mw)
        self.co2_per_fuel_kg = float(co2_per_fuel_kg)
        self.fuel_lhv_j_kg = float(fuel_lhv_j_kg)

        path = Path(table_path) if table_path else _DEFAULT_TABLE_PATH
        self._table = pd.read_csv(path)
        self._size_scale = (rated_power_mw * 1e6) / _DEFAULT_RATED_POWER_W

        gtl = self._table["GTLoad"].to_numpy()
        self._interp_power = interp1d(gtl, self._table["P_el_W"].to_numpy(), kind="linear")
        self._interp_fuel = interp1d(gtl, self._table["fuelFlowRate_kg_s"].to_numpy(), kind="linear")
        self._interp_exh_m = interp1d(gtl, self._table["exhaust_m_flow_kg_s"].to_numpy(), kind="linear")
        self._interp_exh_T = interp1d(gtl, self._table["exhaust_T_K"].to_numpy(), kind="linear")

    def dispatch(self, load: ArrayLike) -> dict:
        """Evaluate plant outputs at the given load setpoint(s).

        Args:
            load: scalar or array-like, GTLoad in [0, 1].

        Returns:
            dict with keys (power_w, fuel_kg_s, efficiency, exhaust_m_kg_s,
            exhaust_T_K, co2_kg_s). Values are floats if `load` was a scalar,
            otherwise numpy arrays.
        """
        arr = np.asarray(load, dtype=float)
        scalar_input = arr.ndim == 0

        if np.any(np.isnan(arr)):
            raise ValueError("load contains NaN")
        if np.any((arr < 0.0) | (arr > 1.0)):
            raise ValueError(
                f"load must be in [0, 1]; got range [{float(arr.min())}, {float(arr.max())}]"
            )

        power_w = self._interp_power(arr) * self._size_scale
        fuel_kg_s = self._interp_fuel(arr) * self._size_scale
        exhaust_m_kg_s = self._interp_exh_m(arr) * self._size_scale
        exhaust_T_K = self._interp_exh_T(arr)

        with np.errstate(divide="ignore", invalid="ignore"):
            denom = fuel_kg_s * self.fuel_lhv_j_kg
            efficiency = np.where(denom > 0, power_w / denom, 0.0)
        co2_kg_s = fuel_kg_s * self.co2_per_fuel_kg

        out = {
            "power_w": power_w,
            "fuel_kg_s": fuel_kg_s,
            "efficiency": efficiency,
            "exhaust_m_kg_s": exhaust_m_kg_s,
            "exhaust_T_K": exhaust_T_K,
            "co2_kg_s": co2_kg_s,
        }
        if scalar_input:
            return {k: float(v) for k, v in out.items()}
        return out

    def dispatch_profile(self, load_series: pd.Series) -> pd.DataFrame:
        """Vectorized dispatch across a time-indexed load profile.

        Args:
            load_series: pandas Series indexed by time, values in [0, 1].

        Returns:
            pandas DataFrame indexed by the input index with the same columns
            as dispatch().
        """
        result = self.dispatch(load_series.to_numpy())
        return pd.DataFrame(result, index=load_series.index)

    def __repr__(self) -> str:
        return f"GasTurbinePlant(rated_power_mw={self.rated_power_mw})"

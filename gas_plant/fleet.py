from __future__ import annotations

from typing import Iterable, Union

import numpy as np
import pandas as pd


class Fleet:
    """Aggregates multiple plant units behind one dispatch API.

    Units are duck-typed: any object with `.dispatch(load)` returning a dict
    of (power_w, fuel_kg_s, efficiency, exhaust_m_kg_s, exhaust_T_K,
    co2_kg_s) and `.dispatch_profile(series)` returning the same as a
    DataFrame will work. This lets the same Fleet hold a mix of
    GasTurbinePlant and (later) CombinedCyclePlant units.
    """

    def __init__(self, units: Iterable):
        self.units = list(units)
        if not self.units:
            raise ValueError("Fleet must have at least one unit")

    def __len__(self) -> int:
        return len(self.units)

    def __repr__(self) -> str:
        return f"Fleet({len(self.units)} units)"

    def _broadcast_loads(self, load) -> np.ndarray:
        arr = np.asarray(load, dtype=float)
        n = len(self.units)
        if arr.ndim == 0:
            return np.full(n, float(arr))
        if arr.shape != (n,):
            raise ValueError(
                f"load must be scalar or 1-D array of length {n}; got shape {arr.shape}"
            )
        return arr

    def dispatch(self, load) -> dict:
        """Dispatch all units at the given load setpoint(s).

        Args:
            load: scalar (applied to all units) or array of length N.

        Returns:
            dict with fleet-aggregate keys (power_w, fuel_kg_s, co2_kg_s,
            exhaust_m_kg_s, exhaust_T_K_mixed, efficiency) and a `units`
            key with the list of per-unit dispatch results.
        """
        per_unit = self._broadcast_loads(load)
        unit_results = [
            u.dispatch(float(l)) for u, l in zip(self.units, per_unit)
        ]
        total_power = sum(r["power_w"] for r in unit_results)
        total_fuel = sum(r["fuel_kg_s"] for r in unit_results)
        total_co2 = sum(r["co2_kg_s"] for r in unit_results)
        total_exh = sum(r["exhaust_m_kg_s"] for r in unit_results)
        weighted_T = sum(
            r["exhaust_m_kg_s"] * r["exhaust_T_K"] for r in unit_results
        )
        # Fleet thermal efficiency from aggregate energy balance — more
        # meaningful than averaging per-unit efficiencies.
        total_fuel_energy = sum(
            r["fuel_kg_s"] * u.fuel_lhv_j_kg
            for r, u in zip(unit_results, self.units)
        )
        return {
            "power_w": total_power,
            "fuel_kg_s": total_fuel,
            "co2_kg_s": total_co2,
            "exhaust_m_kg_s": total_exh,
            "exhaust_T_K_mixed": (
                weighted_T / total_exh if total_exh > 0 else 0.0
            ),
            "efficiency": (
                total_power / total_fuel_energy if total_fuel_energy > 0 else 0.0
            ),
            "units": unit_results,
        }

    def dispatch_profile(
        self, load: Union[pd.Series, pd.DataFrame]
    ) -> pd.DataFrame:
        """Dispatch across a time-indexed load profile.

        Args:
            load: pandas Series (same load broadcast to all units at each
                timestep) OR pandas DataFrame with one column per unit.

        Returns:
            DataFrame indexed by the input time index with fleet-aggregate
            columns: power_w, fuel_kg_s, co2_kg_s, exhaust_m_kg_s,
            exhaust_T_K_mixed, efficiency.
        """
        if isinstance(load, pd.Series):
            unit_dfs = [u.dispatch_profile(load) for u in self.units]
        elif isinstance(load, pd.DataFrame):
            if load.shape[1] != len(self.units):
                raise ValueError(
                    f"DataFrame must have {len(self.units)} columns; "
                    f"got {load.shape[1]}"
                )
            unit_dfs = [
                u.dispatch_profile(load.iloc[:, i])
                for i, u in enumerate(self.units)
            ]
        else:
            raise TypeError("load must be a pandas Series or DataFrame")

        idx = unit_dfs[0].index
        result = pd.DataFrame(index=idx)
        result["power_w"] = sum(df["power_w"] for df in unit_dfs)
        result["fuel_kg_s"] = sum(df["fuel_kg_s"] for df in unit_dfs)
        result["co2_kg_s"] = sum(df["co2_kg_s"] for df in unit_dfs)
        result["exhaust_m_kg_s"] = sum(df["exhaust_m_kg_s"] for df in unit_dfs)
        weighted_T = sum(
            df["exhaust_m_kg_s"] * df["exhaust_T_K"] for df in unit_dfs
        )
        result["exhaust_T_K_mixed"] = np.where(
            result["exhaust_m_kg_s"] > 0,
            weighted_T / result["exhaust_m_kg_s"].replace(0, np.nan),
            0.0,
        )
        total_fuel_energy = sum(
            df["fuel_kg_s"] * u.fuel_lhv_j_kg
            for df, u in zip(unit_dfs, self.units)
        )
        result["efficiency"] = np.where(
            total_fuel_energy > 0,
            result["power_w"] / total_fuel_energy.replace(0, np.nan),
            0.0,
        )
        return result

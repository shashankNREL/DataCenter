"""High-level scenario runners that bundle ANDES + gas_plant post-processing.

Each runner returns a `ScenarioResult` with time-series trajectories and
joins them with gas_plant fuel/CO2 outputs so the caller gets one object
that has both electrical dynamics and economics/emissions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
import andes

from .case_builder import IslandedCaseConfig, build_islanded_test_case


@dataclass
class ScenarioResult:
    """Trajectories from a coupled ANDES + gas_plant scenario."""

    t: np.ndarray                       # time [s]
    freq_gas_hz: np.ndarray             # frequency at gas bus [Hz]
    freq_dc_hz: np.ndarray              # frequency at data-center bus [Hz]
    plant_power_mw: np.ndarray          # actual gas mechanical power [MW]
    line_tie_flow_mw: np.ndarray        # grid-tie line active flow [MW]
    voltage_dc_pu: np.ndarray           # data-center bus voltage [pu]

    # Joined economics/emissions from gas_plant — sampled at each ANDES timestep
    fuel_kg_s: np.ndarray
    co2_kg_s: np.ndarray
    cumulative_fuel_kg: np.ndarray
    cumulative_co2_kg: np.ndarray

    # Scenario context
    config: IslandedCaseConfig
    nominal_freq_hz: float = 60.0

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame({
            "t_s": self.t,
            "freq_gas_hz": self.freq_gas_hz,
            "freq_dc_hz": self.freq_dc_hz,
            "plant_power_mw": self.plant_power_mw,
            "line_tie_flow_mw": self.line_tie_flow_mw,
            "voltage_dc_pu": self.voltage_dc_pu,
            "fuel_kg_s": self.fuel_kg_s,
            "co2_kg_s": self.co2_kg_s,
            "cumulative_fuel_kg": self.cumulative_fuel_kg,
            "cumulative_co2_kg": self.cumulative_co2_kg,
        }).set_index("t_s")

    def summary(self) -> dict:
        nadir_idx = int(np.argmin(self.freq_gas_hz))
        peak_idx = int(np.argmax(self.freq_gas_hz))
        return {
            "freq_nadir_hz": float(self.freq_gas_hz[nadir_idx]),
            "freq_nadir_time_s": float(self.t[nadir_idx]),
            "freq_peak_hz": float(self.freq_gas_hz[peak_idx]),
            "freq_peak_time_s": float(self.t[peak_idx]),
            "freq_final_hz": float(self.freq_gas_hz[-1]),
            "max_excursion_hz": float(
                max(self.freq_gas_hz.max() - self.nominal_freq_hz,
                    self.nominal_freq_hz - self.freq_gas_hz.min())
            ),
            "total_fuel_kg": float(self.cumulative_fuel_kg[-1]),
            "total_co2_kg": float(self.cumulative_co2_kg[-1]),
            "duration_s": float(self.t[-1] - self.t[0]),
        }


def run_islanding_scenario(
    cfg: IslandedCaseConfig,
    duration_s: float = 30.0,
    tstep_s: float = 0.01,
) -> ScenarioResult:
    """Build the case, simulate, and return joined trajectories."""
    ss = build_islanded_test_case(cfg)

    ss.TDS.config.tf = duration_s
    ss.TDS.config.tstep = tstep_s
    ss.TDS.run()
    if not ss.TDS.converged:
        raise RuntimeError("TDS did not converge — check case setup or step size")

    t = np.array(ss.dae.ts.t)
    y = np.array(ss.dae.ts.y)

    sysbase = float(ss.config.mva)
    nominal = 60.0

    # Read the gas rotor frequency directly from GENCLS.omega (state),
    # which is the authoritative "is the machine spinning at 60 Hz?" signal.
    x = np.array(ss.dae.ts.x)
    omega_pu = x[:, ss.GENCLS.omega.a[0]]
    freq_gas_pu = omega_pu

    # BusFreq.f at the DC bus is a low-pass-filtered estimate from the bus
    # angle derivative. Useful for verifying voltage-side behavior but noisy
    # under fast transients; we keep it for completeness.
    # Buses were added in order: GAS, DC, GRID.
    freq_dc_pu = y[:, ss.BusFreq.f.a[1]]

    # GENCLS.tm is mechanical torque in pu on the SYSTEM MVA base. Near
    # nominal speed P_mech_pu_sys ≈ tm.
    plant_pm_pu_sys = y[:, ss.GENCLS.tm.a[0]]
    plant_power_mw = plant_pm_pu_sys * sysbase

    # Tie-line flow computed from bus angles, then masked to 0 while the
    # line is out of service (between islanding and resync events).
    X_tie = ss.Line.x.v[1]
    V_dc = y[:, ss.Bus.v.a[1]]
    V_grid = y[:, ss.Bus.v.a[2]]
    a_dc = y[:, ss.Bus.a.a[1]]
    a_grid = y[:, ss.Bus.a.a[2]]
    line_tie_flow_pu = (V_dc * V_grid / X_tie) * np.sin(a_dc - a_grid)
    line_tie_flow_mw = line_tie_flow_pu * sysbase
    if cfg.island_time_s is not None and cfg.resync_time_s is not None:
        in_island = (t >= cfg.island_time_s) & (t < cfg.resync_time_s)
        line_tie_flow_mw = np.where(in_island, 0.0, line_tie_flow_mw)
    elif cfg.island_time_s is not None:
        line_tie_flow_mw = np.where(t >= cfg.island_time_s, 0.0, line_tie_flow_mw)

    voltage_dc_pu = V_dc

    # Join gas_plant fuel/CO2 by mapping ANDES power output back to GTLoad
    # via a quick inverse interpolation over the surrogate table.
    plant = cfg.plant
    # Construct (load → power_w) lookup from the surrogate, then invert
    load_grid = np.linspace(0, 1, 201)
    power_grid = np.array([plant.dispatch(float(l))["power_w"] for l in load_grid]) / 1e6
    # Clamp instantaneous mechanical power to the table range
    clamped_p_mw = np.clip(plant_power_mw, power_grid.min(), power_grid.max())
    inferred_load = np.interp(clamped_p_mw, power_grid, load_grid)

    fuel_kg_s = np.array([plant.dispatch(float(l))["fuel_kg_s"] for l in inferred_load])
    co2_kg_s = np.array([plant.dispatch(float(l))["co2_kg_s"] for l in inferred_load])

    # Cumulative integrals (trapezoidal)
    cumulative_fuel_kg = np.concatenate([
        [0.0],
        np.cumsum(0.5 * (fuel_kg_s[1:] + fuel_kg_s[:-1]) * np.diff(t))
    ])
    cumulative_co2_kg = np.concatenate([
        [0.0],
        np.cumsum(0.5 * (co2_kg_s[1:] + co2_kg_s[:-1]) * np.diff(t))
    ])

    return ScenarioResult(
        t=t,
        freq_gas_hz=freq_gas_pu * nominal,
        freq_dc_hz=freq_dc_pu * nominal,
        plant_power_mw=plant_power_mw,
        line_tie_flow_mw=line_tie_flow_mw,
        voltage_dc_pu=voltage_dc_pu,
        fuel_kg_s=fuel_kg_s,
        co2_kg_s=co2_kg_s,
        cumulative_fuel_kg=cumulative_fuel_kg,
        cumulative_co2_kg=cumulative_co2_kg,
        config=cfg,
        nominal_freq_hz=nominal,
    )

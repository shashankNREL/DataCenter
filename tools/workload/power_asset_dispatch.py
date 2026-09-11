"""Constrained, one-second storage dispatch for the scheduler screening study.

Positive battery power supplies the facility. The remaining electrical load,
not an assumed mechanical turbine output, is replayed through GGOV1.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class AssetSettings:
    battery_power_mw: float = 6.0
    battery_energy_mwh: float = 1.5
    min_soc: float = 0.1
    max_soc: float = 0.9
    initial_soc: float = 0.5
    charge_efficiency: float = 0.95
    discharge_efficiency: float = 0.95
    generator_reserve_mw: float = 2.0
    generator_ramp_mw_s: float = 0.5
    fuel_cost_per_kg: float = 0.25
    battery_cost_per_mwh: float = 20.0
    recovery_s: int = 120

    def __post_init__(self) -> None:
        positive = (self.battery_power_mw, self.battery_energy_mwh,
                    self.generator_ramp_mw_s, self.fuel_cost_per_kg,
                    self.battery_cost_per_mwh)
        if any(not np.isfinite(value) or value <= 0 for value in positive):
            raise ValueError("asset ratings, ramp limit, and costs must be positive")
        if not 0 <= self.min_soc < self.initial_soc < self.max_soc <= 1:
            raise ValueError("initial charge must lie strictly inside storage bounds")
        if not (0 < self.charge_efficiency <= 1 and 0 < self.discharge_efficiency <= 1):
            raise ValueError("storage efficiencies must lie in (0, 1]")
        if not np.isfinite(self.generator_reserve_mw) or self.generator_reserve_mw < 0:
            raise ValueError("generator reserve must be finite and nonnegative")
        if not isinstance(self.recovery_s, int) or self.recovery_s < 1:
            raise ValueError("recovery duration must be a positive integer")


def dispatch_storage(
    demand_mw: np.ndarray,
    tau_s: float,
    settings: AssetSettings,
    generator_max_mw: float,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Apply the same causal smoothing/recharge rule to every schedule.

    Samples are left-held over [k, k+1); the final row is only an endpoint.
    A final quiet recovery period restores initial stored energy when feasible.
    Storage saturation is physical; resulting generator violations are reported,
    never hidden by clipping the remaining electrical demand.
    """
    demand = np.asarray(demand_mw, dtype=float)
    if (demand.ndim != 1 or demand.size < settings.recovery_s + 2
            or not np.isfinite(demand).all() or np.any(demand < 0)):
        raise ValueError("demand must be a finite nonnegative one-second trace with recovery")
    if not np.isfinite(tau_s) or tau_s < 0:
        raise ValueError("smoothing time must be finite and nonnegative")
    if (not np.isfinite(generator_max_mw)
            or generator_max_mw <= settings.generator_reserve_mw):
        raise ValueError("generator capacity must exceed reserved headroom")
    recovery_start = demand.size - 1 - settings.recovery_s
    if not np.allclose(demand[recovery_start:], demand[-1], rtol=0, atol=1e-9):
        raise ValueError("recovery period must contain only the constant background")

    capacity = settings.battery_energy_mwh
    lower, upper = settings.min_soc * capacity, settings.max_soc * capacity
    initial = settings.initial_soc * capacity
    energy = np.empty(demand.size)
    energy[0] = initial
    battery = np.zeros(demand.size)
    requested = np.zeros(demand.size)
    target = demand[0]
    net_load = demand.copy()
    saturation_s = 0
    eta_c, eta_d = settings.charge_efficiency, settings.discharge_efficiency
    for k in range(demand.size - 1):
        target = demand[k] if tau_s == 0 else target + (demand[k] - target) / (tau_s + 1.0)
        requested[k] = demand[k] - target
        if k >= recovery_start:
            # Equal energy restoration over the remaining quiet intervals.
            remaining = demand.size - 1 - k
            delta = initial - energy[k]
            requested[k] = (-delta * 3600 / eta_c / remaining if delta >= 0
                            else -delta * 3600 * eta_d / remaining)
        max_discharge = min(settings.battery_power_mw,
                            max(0.0, (energy[k] - lower) * eta_d * 3600),
                            demand[k])
        max_charge = min(settings.battery_power_mw,
                         max(0.0, (upper - energy[k]) * 3600 / eta_c),
                         max(0.0, generator_max_mw - settings.generator_reserve_mw
                             - demand[k]))
        battery[k] = np.clip(requested[k], -max_charge, max_discharge)
        saturation_s += abs(battery[k] - requested[k]) > 1e-8
        discharge, charge = max(battery[k], 0.0), max(-battery[k], 0.0)
        energy[k + 1] = energy[k] + (eta_c * charge - discharge / eta_d) / 3600
        net_load[k] = demand[k] - battery[k]

    discharge_mwh = float(np.maximum(battery[:-1], 0).sum() / 3600)
    charge_mwh = float(np.maximum(-battery[:-1], 0).sum() / 3600)
    ramp = np.diff(net_load)
    frame = pd.DataFrame({
        "generator_electrical_load_mw": net_load,
        "battery_power_mw": battery,
        "battery_requested_mw": requested,
        "stored_energy_mwh": energy,
        "state_of_charge": energy / capacity,
    })
    return frame, {
        "tau_s": tau_s,
        "battery_peak_mw": float(np.max(np.abs(battery))),
        "battery_energy_swing_kwh": float(np.ptp(energy) * 1000),
        "battery_charge_mwh": charge_mwh,
        "battery_discharge_mwh": discharge_mwh,
        "battery_throughput_mwh": charge_mwh + discharge_mwh,
        "battery_loss_mwh": charge_mwh * (1 - eta_c) + discharge_mwh * (1 / eta_d - 1),
        "terminal_energy_error_kwh": float((energy[-1] - initial) * 1000),
        "battery_saturation_s": float(saturation_s),
        "generator_max_ramp_mw_s": float(np.max(np.abs(ramp))),
        "generator_ramp_violation_s": float(np.sum(
            np.abs(ramp) > settings.generator_ramp_mw_s + 1e-8)),
        "generator_reserve_violation_s": float(np.sum(
            net_load[:-1] > generator_max_mw - settings.generator_reserve_mw + 1e-8)),
        "power_balance_error_mw": float(np.max(np.abs(net_load + battery - demand))),
    }

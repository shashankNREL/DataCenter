"""Tier A (legacy): swing equation + standard TGOV1 governor for the LM2500.

V&V Phase 0 restructure (see docs/vv_log.md, fix G4): the block layout now
matches the standard TGOV1 exactly (PES-TR1 §2.2 / PSS/E):

                 1                (1 + s T2)
  (Pref - dw)/R ----> [ 1/(1+sT1) ] --clip[VMIN,VMAX]--> [ ---------- ] --> Pm
                       (valve, non-windup limits          (1 + s T3)
                        + optional rate limit)                    - Dt*dw

The pre-fix implementation applied a (1+sT2)/(1+sT1) lead-lag, clipped its
OUTPUT, then rate-limited through an extra first-order chase with time
constant T1 — introducing a spurious second pole at 1/T1 that standard
TGOV1 does not have, and placing the limiter at a nonstandard location.
Both defects are removed. The valve state itself now carries the
VMIN/VMAX non-windup limits (which IS the standard anti-windup — the
separate `use_anti_windup` flag is gone) and the optional rate limit acts
directly on dvalve/dt.

Per-unit base: generator MVA (Sn_mva) throughout — valid for TGOV1 because
its implicit turbine gain is 1 (valve pu = power pu), so VMAX = 22/23
genuinely caps Pm at the 22 MW turbine rating. TGOV1 is retained as the
LEGACY Tier A model; GGOV1 (turbine-base, PES-TR1 §3.3) is the reference
governor.

Retained Tier-A behaviors:
  A2  Valve rate limit (optional, on dvalve/dt)
  A3  Frequency-dependent load damping Pe = Pe_load*(1 + alpha*(w-1))
  A5  Fuel reported through a separate combustor lag on the valve signal
  A6  Event-driven (chunked) integration across load-step boundaries

State vector (5 states):
    delta    rotor angle (rad)
    omega    rotor speed (pu on 2*pi*60)
    valve    governor/valve state after 1/(1+sT1), non-windup [VMIN,VMAX]
    x2       turbine lead-lag state (T3 lag)
    P_fuel   combustor lag on valve (fuel reporting)

Pm is algebraic: Pm = x2 + (T2/T3)*(valve - x2) - Dt*(omega - 1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import pandas as pd
from scipy.integrate import solve_ivp


# ---------------------------------------------------------------------------
# Parameters and state containers
# ---------------------------------------------------------------------------

@dataclass
class TGOV1Params:
    """Per-unit parameters for the standard-structure TGOV1 LM2500 model.

    Per-unit base is the generator MVA (`Sn_mva`); TGOV1's implicit turbine
    gain of 1 makes valve pu = power pu on that base.
    """

    # ----- Machine -----
    Sn_mva: float = 23.0      # generator MVA base
    H_s: float = 2.8          # inertia constant (s) — status ESTIMATED
    # D = 0: load damping is modeled explicitly via alpha_load_damping
    # (V&V fix G5 — avoids double counting).
    D_pu: float = 0.0

    # ----- Turbine rating (for VMAX rebase) -----
    P_turbine_mw: float = 22.0  # LM2500 base rating

    # ----- Governor / turbine -----
    R_droop: float = 0.04     # 4 % droop (aero islanded)
    T1_s: float = 0.15        # governor/valve lag
    T2_s: float = 0.30        # turbine lead
    T3_s: float = 1.50        # turbine lag (gas-fill)
    Dt_pu: float = 0.0        # turbine damping (standard TGOV1 Dt)

    # ----- Valve limits (valid as POWER limits here: implicit Kturb = 1) -----
    vmax_pu: float = 22.0 / 23.0  # turbine rating on gen base
    vmin_pu: float = 0.15         # engine lean-blowout floor (= 3.45 MW)

    # ----- Options -----
    # Standard TGOV1 has NO valve rate limit. Enabling one here places a
    # slew limiter inside the single-lag droop loop; at LM2500-like gains
    # (R=0.04, T1=0.15) that produces a sustained slew-induced limit cycle
    # (~ +/-1.3 Hz observed) — a known describing-function instability, not
    # a numerical artifact. Rate-limited fuel dynamics belong to GGOV1,
    # where the MaxERR/MinERR clamp bounds the commanded rate. Default OFF
    # (V&V Phase 0; see docs/vv_log.md, deviation D5).
    use_valve_rate_limit: bool = False
    vrmax_pu_s: float = 0.10          # valve open-rate limit (pu/s)
    vrmin_pu_s: float = -0.10         # valve close-rate limit (pu/s)
    alpha_load_damping: float = 1.5   # Kundur §11.1.4 typical
    T_comb_s: float = 0.30            # combustor + fuel-injection lag
    chunked: bool = True              # event-driven integration
    # Swing-equation formulation. False (default): power form,
    # M dw/dt = Pm - Pe. True: strict torque form M dw/dt = (Pm - Pe)/w.
    # V&V NOTE: ANDES GENCLS uses the standard speed-voltage approximation
    # (stator flux at w = 1), under which tau_e = Pe and the governor
    # output enters tm one-to-one — i.e. ANDES's effective swing equation
    # IS the power form. The cross-check (tools/vv/crosscheck_andes_tgov1.py)
    # therefore runs with torque_form=False and matches ANDES to ~1 mHz.
    torque_form: bool = False

    # ----- Integrator -----
    rtol: float = 1e-6
    atol: float = 1e-8
    max_step_s: float = 0.5

    # ----- Derived (cached) -----
    omega_0_rad_s: float = field(default=2.0 * np.pi * 60.0, init=False)
    M_pu_s: float = field(init=False)

    def __post_init__(self) -> None:
        self.M_pu_s = 2.0 * self.H_s


@dataclass
class TGOV1State:
    """ODE state, per-unit on Sn_mva."""

    delta: float = 0.0
    omega: float = 1.0
    valve: float = 0.0
    x2: float = 0.0
    P_fuel: float = 0.0

    def as_array(self) -> np.ndarray:
        return np.array([self.delta, self.omega, self.valve,
                         self.x2, self.P_fuel], dtype=float)

    @classmethod
    def from_array(cls, y: np.ndarray) -> "TGOV1State":
        return cls(delta=float(y[0]), omega=float(y[1]), valve=float(y[2]),
                   x2=float(y[3]), P_fuel=float(y[4]))


@dataclass
class TGOV1Result:
    """Time-resolved simulation result on a uniform grid.

    `Pe_mw` is the ACTUAL electrical power including load damping;
    `Pe_demand_mw` is the raw ZOH demand.
    """

    t_s: np.ndarray
    delta_rad: np.ndarray
    omega_pu: np.ndarray
    freq_hz: np.ndarray
    speed_rpm: np.ndarray
    Pm_mw: np.ndarray
    Pm_pu: np.ndarray
    Pe_mw: np.ndarray
    Pe_pu: np.ndarray
    Pe_demand_mw: np.ndarray
    valve_pu: np.ndarray
    P_fuel_pu: np.ndarray
    fuel_kg_s: Optional[np.ndarray] = None
    cum_fuel_kg: Optional[np.ndarray] = None

    def as_dataframe(self) -> pd.DataFrame:
        cols = {
            "t_s": self.t_s,
            "delta_rad": self.delta_rad,
            "omega_pu": self.omega_pu,
            "freq_hz": self.freq_hz,
            "speed_rpm": self.speed_rpm,
            "Pm_mw": self.Pm_mw,
            "Pm_pu": self.Pm_pu,
            "Pe_mw": self.Pe_mw,
            "Pe_pu": self.Pe_pu,
            "Pe_demand_mw": self.Pe_demand_mw,
            "valve_pu": self.valve_pu,
            "P_fuel_pu": self.P_fuel_pu,
        }
        if self.fuel_kg_s is not None:
            cols["fuel_kg_s"] = self.fuel_kg_s
        if self.cum_fuel_kg is not None:
            cols["cum_fuel_kg"] = self.cum_fuel_kg
        return pd.DataFrame(cols)


# ---------------------------------------------------------------------------
# RHS
# ---------------------------------------------------------------------------

def _pm_algebraic(valve: np.ndarray, x2: np.ndarray, omega: np.ndarray,
                  p: TGOV1Params) -> np.ndarray:
    """Pm = lead-lag output - Dt*dw (works on scalars and arrays)."""
    return x2 + (p.T2_s / p.T3_s) * (valve - x2) - p.Dt_pu * (omega - 1.0)


def _rhs_factory(params: TGOV1Params, Pref: float, Pe_load_pu: float) -> Callable:
    """Build the RHS closure for a constant-load chunk."""
    p = params

    def rhs(t: float, y: np.ndarray) -> np.ndarray:
        delta, omega, valve, x2, P_fuel = y

        # ---- Load with frequency damping ----
        Pe = Pe_load_pu * (1.0 + p.alpha_load_damping * (omega - 1.0))

        # ---- Governor: droop input through 1/(1+sT1) ----
        gov_in = Pref + (1.0 - omega) / p.R_droop
        dvalve = (gov_in - valve) / p.T1_s
        if p.use_valve_rate_limit:
            dvalve = float(np.clip(dvalve, p.vrmin_pu_s, p.vrmax_pu_s))
        # Non-windup position limits on the valve state (standard TGOV1)
        if (valve >= p.vmax_pu and dvalve > 0) or (valve <= p.vmin_pu and dvalve < 0):
            dvalve = 0.0

        # ---- Turbine lead-lag (1+sT2)/(1+sT3) ----
        dx2 = (valve - x2) / p.T3_s
        Pm = _pm_algebraic(valve, x2, omega, p)

        # ---- Swing equation ----
        if p.torque_form:
            domega = ((Pm - Pe) / omega - p.D_pu * (omega - 1.0)) / p.M_pu_s
        else:
            domega = (Pm - Pe - p.D_pu * (omega - 1.0)) / p.M_pu_s
        ddelta = p.omega_0_rad_s * (omega - 1.0)

        # ---- Combustor lag for fuel reporting ----
        dP_fuel = (valve - P_fuel) / p.T_comb_s

        return np.array([ddelta, domega, dvalve, dx2, dP_fuel])

    return rhs


# ---------------------------------------------------------------------------
# Driver: event-driven (chunked) integration over a Load17-style profile
# ---------------------------------------------------------------------------

def simulate_tgov1(
    load_time_s: np.ndarray,
    load_demand_mw: np.ndarray,
    params: TGOV1Params = TGOV1Params(),
    sample_dt_s: float = 1.0,
    dispatch_fn: Optional[Callable[[np.ndarray], dict]] = None,
) -> TGOV1Result:
    """Run the standard-structure TGOV1 ODE over a ZOH load profile (MW)."""

    load_time_s = np.asarray(load_time_s, dtype=float)
    load_demand_mw = np.asarray(load_demand_mw, dtype=float)
    if load_time_s.shape != load_demand_mw.shape:
        raise ValueError("load_time_s and load_demand_mw must be the same shape")
    if not np.all(np.diff(load_time_s) > 0):
        raise ValueError("load_time_s must be strictly increasing")
    if load_time_s.size < 2:
        raise ValueError("need at least two load samples")

    p = params
    Sn = p.Sn_mva
    load_pu = load_demand_mw / Sn

    if load_time_s[0] > 0:
        load_time_s = np.concatenate([[0.0], load_time_s])
        load_pu = np.concatenate([[load_pu[0]], load_pu])

    # ---- Initial condition: steady state at the first load point ----
    Pe0 = float(load_pu[0])
    if Pe0 > p.vmax_pu:
        raise ValueError(
            f"Initial load {Pe0 * Sn:.2f} MW exceeds turbine rating "
            f"{p.vmax_pu * Sn:.2f} MW. Refusing to start above VMAX."
        )
    if Pe0 < p.vmin_pu:
        raise ValueError(
            f"Initial load {Pe0 * Sn:.2f} MW is below the minimum-fuel floor "
            f"{p.vmin_pu * Sn:.2f} MW."
        )
    Pref = Pe0
    state = TGOV1State(delta=0.0, omega=1.0, valve=Pe0, x2=Pe0, P_fuel=Pe0)

    t_eval = np.arange(0.0, load_time_s[-1] + sample_dt_s, sample_dt_s)
    t_eval = t_eval[t_eval <= load_time_s[-1] + 1e-9]
    y_eval = np.empty((5, t_eval.size), dtype=float)
    y_eval[:, 0] = state.as_array()
    demand_pu_eval = np.empty(t_eval.size, dtype=float)
    demand_pu_eval[0] = Pe0
    eval_idx = 1

    if p.chunked:
        sol = None
        for k in range(load_time_s.size - 1):
            t0 = float(load_time_s[k])
            t1 = float(load_time_s[k + 1])
            Pe_chunk = float(load_pu[k])
            rhs = _rhs_factory(p, Pref, Pe_chunk)
            sol = solve_ivp(
                rhs, (t0, t1), state.as_array(),
                method="RK45",
                max_step=p.max_step_s,
                rtol=p.rtol, atol=p.atol,
                dense_output=True,
            )
            if not sol.success:
                raise RuntimeError(f"solve_ivp failed in [{t0}, {t1}]: {sol.message}")
            while eval_idx < t_eval.size and t_eval[eval_idx] <= t1 + 1e-9:
                y_eval[:, eval_idx] = sol.sol(t_eval[eval_idx])
                demand_pu_eval[eval_idx] = Pe_chunk
                eval_idx += 1
            state = TGOV1State.from_array(sol.y[:, -1])
        while eval_idx < t_eval.size:
            y_eval[:, eval_idx] = sol.sol(min(t_eval[eval_idx], load_time_s[-1]))
            demand_pu_eval[eval_idx] = float(load_pu[-2])
            eval_idx += 1
    else:
        from scipy.interpolate import interp1d
        Pe_interp = interp1d(
            load_time_s, load_pu, kind="zero",
            bounds_error=False, fill_value=(load_pu[0], load_pu[-1]),
        )

        def rhs_baseline(t: float, y: np.ndarray) -> np.ndarray:
            return _rhs_factory(p, Pref, float(Pe_interp(t)))(t, y)

        sol = solve_ivp(
            rhs_baseline, (0.0, float(load_time_s[-1])), state.as_array(),
            method="RK45",
            max_step=p.max_step_s,
            rtol=p.rtol, atol=p.atol,
            dense_output=True,
        )
        if not sol.success:
            raise RuntimeError(f"baseline solve_ivp failed: {sol.message}")
        y_eval = sol.sol(t_eval)
        demand_pu_eval = np.asarray(Pe_interp(t_eval), dtype=float)

    # ---- Post-process into physical units ----
    delta_rad = y_eval[0]
    omega_pu = y_eval[1]
    valve_pu = y_eval[2]
    x2 = y_eval[3]
    P_fuel_pu = y_eval[4]

    freq_hz = omega_pu * 60.0
    speed_rpm = freq_hz * 60.0
    Pm_pu = _pm_algebraic(valve_pu, x2, omega_pu, p)
    Pm_mw = Pm_pu * Sn

    Pe_pu = demand_pu_eval * (1.0 + p.alpha_load_damping * (omega_pu - 1.0))
    Pe_mw = Pe_pu * Sn
    Pe_demand_mw = demand_pu_eval * Sn

    result = TGOV1Result(
        t_s=t_eval,
        delta_rad=delta_rad,
        omega_pu=omega_pu,
        freq_hz=freq_hz,
        speed_rpm=speed_rpm,
        Pm_mw=Pm_mw,
        Pm_pu=Pm_pu,
        Pe_mw=Pe_mw,
        Pe_pu=Pe_pu,
        Pe_demand_mw=Pe_demand_mw,
        valve_pu=valve_pu,
        P_fuel_pu=P_fuel_pu,
    )

    # ---- Fuel: valve pu = power pu here (implicit Kturb = 1), so the
    #      surrogate load-fraction mapping is valid as-is ----
    if dispatch_fn is not None:
        load_frac = np.clip(P_fuel_pu * Sn / p.P_turbine_mw, 0.0, 1.0)
        disp = dispatch_fn(load_frac)
        result.fuel_kg_s = np.asarray(disp["fuel_kg_s"], dtype=float)
        result.cum_fuel_kg = np.concatenate(
            [[0.0],
             np.cumsum(0.5 * (result.fuel_kg_s[1:] + result.fuel_kg_s[:-1])
                       * np.diff(t_eval))]
        )

    return result

"""Tier A: cleaned-up swing-equation + TGOV1 governor for the LM2500.

Bug-fixes applied relative to the inline ODE in notebooks/lm2500_model.ipynb
cells 25-27 (per docs/tier_plan.md, Tier A change log):

  A1  Anti-windup on the governor lag state x1 when P_valve clips.
  A2  Valve rate limit (P_valve becomes a state, dP_valve/dt clipped).
  A3  Frequency-dependent load damping: Pe(t,omega) = Pe_load(t) * (1 + alpha*(omega-1)).
  A4  VMAX rebased so the valve cannot exceed the turbine thermal limit
      (0.957 pu on the 23 MVA generator base, i.e. 22 MW).
  A5  Fuel computed from the valve command (with a separate combustor lag),
      NOT from Pm.  Removes the double-counted turbine dynamics.
  A6  Event-driven integration: solve the ODE chunk-by-chunk between Load17
      sample times so the solver never straddles a ZOH discontinuity.

The original Tier-0 (baseline) behavior is preserved if you set
    use_anti_windup = False
    use_valve_rate_limit = False
    alpha_load_damping = 0.0
    vmax_pu = 1.1
    vmin_pu = 0.0
    fuel_from_valve = False
    chunked = False
in TGOV1Params - useful for A/B diff plots.

State vector (in order):
    delta    - rotor angle (rad)
    omega    - rotor speed, per-unit on omega_0 = 2*pi*60 rad/s
    x1       - governor lag state (pu, governor MW base)
    P_valve  - fuel-valve command after rate limit (pu)        [A2]
    P_fuel   - filtered valve, the signal that feeds dispatch  [A5]
    Pm       - mechanical power output of the turbine (pu)
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
    """Per-unit parameters for the cleaned-up LM2500 TGOV1 model.

    Per-unit base is the generator MVA (`Sn_mva`), so all power-like signals
    (Pm, Pe, P_valve, etc.) are in pu on `Sn_mva`.

    Defaults match the original notebook (cell 25) except where annotated [Ai].
    """

    # ----- Machine -----
    Sn_mva: float = 23.0      # generator MVA base
    H_s: float = 2.8          # inertia constant (s)
    D_pu: float = 1.0         # lumped electromechanical damping (pu/pu)

    # ----- Turbine thermal rating (for VMAX rebase, A4) -----
    P_turbine_mw: float = 22.0  # LM2500 base thermal/shaft rating

    # ----- Governor -----
    R_droop: float = 0.04     # 4 % droop (aero islanded)
    T1_s: float = 0.15        # governor lag (fast electronic valve)
    T2_s: float = 0.30        # lead time constant
    T3_s: float = 1.50        # turbine gas-fill lag

    # ----- Fuel-valve limits -----
    vmax_pu: float = 22.0 / 23.0  # [A4] turbine thermal limit on gen base
    vmin_pu: float = 0.15         # engine lean-blowout floor

    # ----- Tier A additions -----
    use_anti_windup: bool = True      # [A1]
    use_valve_rate_limit: bool = True # [A2]
    vrmax_pu_s: float = 0.10          # [A2] valve open-rate limit (pu/s)
    vrmin_pu_s: float = -0.10         # [A2] valve close-rate limit (pu/s)
    alpha_load_damping: float = 1.5   # [A3] frequency-dependent load damping
    fuel_from_valve: bool = True      # [A5] fuel = f(P_fuel), not f(Pm)
    T_comb_s: float = 0.30            # [A5] combustor + fuel-injection lag
    chunked: bool = True              # [A6] event-driven integration

    # ----- Integrator -----
    rtol: float = 1e-4
    atol: float = 1e-6
    max_step_s: float = 1.0

    # ----- Derived (cached) -----
    omega_0_rad_s: float = field(default=2.0 * np.pi * 60.0, init=False)
    M_pu_s: float = field(init=False)

    def __post_init__(self) -> None:
        self.M_pu_s = 2.0 * self.H_s


@dataclass
class TGOV1State:
    """Initial / instantaneous ODE state, in the per-unit base of TGOV1Params."""

    delta: float = 0.0       # rotor angle (rad)
    omega: float = 1.0       # speed (pu)
    x1: float = 0.0          # governor lag state (pu)
    P_valve: float = 0.0     # valve command (pu)
    P_fuel: float = 0.0      # filtered valve / combustor state (pu)
    Pm: float = 0.0          # mechanical power (pu)

    def as_array(self) -> np.ndarray:
        return np.array([self.delta, self.omega, self.x1,
                         self.P_valve, self.P_fuel, self.Pm], dtype=float)

    @classmethod
    def from_array(cls, y: np.ndarray) -> "TGOV1State":
        return cls(delta=float(y[0]), omega=float(y[1]), x1=float(y[2]),
                   P_valve=float(y[3]), P_fuel=float(y[4]), Pm=float(y[5]))


@dataclass
class TGOV1Result:
    """Time-resolved simulation result on a uniform 1 s grid (or as requested).

    All physical-unit columns are derived after the ODE solve. `*_pu` columns
    are on Sn_mva = generator base.
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
    P_valve_pu: np.ndarray
    P_fuel_pu: np.ndarray
    x1_pu: np.ndarray
    fuel_kg_s: Optional[np.ndarray] = None   # filled by post-processing
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
            "P_valve_pu": self.P_valve_pu,
            "P_fuel_pu": self.P_fuel_pu,
            "x1_pu": self.x1_pu,
        }
        if self.fuel_kg_s is not None:
            cols["fuel_kg_s"] = self.fuel_kg_s
        if self.cum_fuel_kg is not None:
            cols["cum_fuel_kg"] = self.cum_fuel_kg
        return pd.DataFrame(cols)


# ---------------------------------------------------------------------------
# RHS
# ---------------------------------------------------------------------------

def _rhs_factory(params: TGOV1Params, Pref: float, Pe_load_pu: float) -> Callable:
    """Build the RHS closure for a constant-load chunk.

    `Pe_load_pu` is the LOAD17 demand on this interval (constant within the
    chunk; the event-driven outer loop swaps it between chunks).
    `Pref` is the governor power reference (set to the initial steady-state
    Pe so the steady-state error is zero at t=0).
    """

    R = params.R_droop
    T1 = params.T1_s
    T2 = params.T2_s
    T3 = params.T3_s
    M = params.M_pu_s
    D = params.D_pu
    omega_0 = params.omega_0_rad_s
    vmax = params.vmax_pu
    vmin = params.vmin_pu
    vrmax = params.vrmax_pu_s
    vrmin = params.vrmin_pu_s
    alpha = params.alpha_load_damping
    T_comb = params.T_comb_s
    use_rate_limit = params.use_valve_rate_limit
    use_anti_windup = params.use_anti_windup

    def rhs(t: float, y: np.ndarray) -> np.ndarray:
        delta, omega, x1, P_valve, P_fuel, Pm = y

        # ---- [A3] Frequency-dependent load damping ----
        # Pe rises (slightly) as ω rises; falls as ω drops. Worst case
        # alpha=0 reproduces the original constant-power load.
        Pe = Pe_load_pu * (1.0 + alpha * (omega - 1.0))

        # ---- Swing equation ----
        domega = (Pm - Pe - D * (omega - 1.0)) / M
        ddelta = omega_0 * (omega - 1.0)

        # ---- TGOV1 governor: lead-lag through x1 ----
        gov_input = Pref + (1.0 - omega) / R
        dx1_unsat = (gov_input - x1) / T1

        # Lead-lag output (the would-be valve command before clip / rate-limit)
        P_valve_target = x1 + T2 * dx1_unsat

        # ---- [A1] Anti-windup ----
        # If the target is outside [vmin, vmax] AND would push us further out,
        # freeze x1.
        P_valve_target_clipped = np.clip(P_valve_target, vmin, vmax)
        saturating_up = (P_valve_target > vmax) and (dx1_unsat > 0)
        saturating_dn = (P_valve_target < vmin) and (dx1_unsat < 0)
        if use_anti_windup and (saturating_up or saturating_dn):
            dx1 = 0.0
        else:
            dx1 = dx1_unsat

        # ---- [A2] Valve rate limit ----
        # P_valve is now a STATE: dP_valve/dt = clip(rate toward target, [vrmin, vrmax]).
        # The first-order pursuit toward P_valve_target_clipped is hand-tuned
        # so that, without rate-limit clipping, P_valve == P_valve_target.
        if use_rate_limit:
            # Rate to instantaneously hit the (clipped) target this step:
            # treat as an algebraic-loop-friendly first-order chase.
            rate_unconstrained = (P_valve_target_clipped - P_valve) / max(T1, 1e-6)
            dP_valve = float(np.clip(rate_unconstrained, vrmin, vrmax))
        else:
            # No rate-limit mode: P_valve algebraically equals the target.
            # We still keep P_valve as a state but force it to track exactly.
            dP_valve = (P_valve_target_clipped - P_valve) / max(T1 * 0.1, 1e-6)

        # ---- [A5] Fuel signal: separate combustor lag from P_valve ----
        # P_fuel tracks P_valve through a fast combustor + fuel-injection lag.
        # Mechanical power Pm tracks P_valve through the slower turbine lag T3.
        dP_fuel = (P_valve - P_fuel) / T_comb
        dPm = (P_valve - Pm) / T3

        return np.array([ddelta, domega, dx1, dP_valve, dP_fuel, dPm])

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
    """Run the Tier-A TGOV1 ODE over a Load17-style step profile.

    Args:
        load_time_s:    1-D array of demand sample times (seconds, increasing).
        load_demand_mw: 1-D array of demand in MW at each sample time.
                        Treated as a zero-order-hold signal between samples.
        params:         TGOV1Params (defaults match the cleaned-up Tier A model).
        sample_dt_s:    Output uniform grid spacing for the returned result.
        dispatch_fn:    Optional callable `dispatch(load_frac_array) -> dict`
                        (matching gas_plant.GasTurbinePlant.dispatch interface)
                        used to compute fuel_kg_s and cum_fuel_kg from the
                        per-unit fuel signal (Tier A wires this to P_fuel,
                        not Pm — A5).

    Returns:
        TGOV1Result with everything in physical units + per-unit columns.
    """

    load_time_s = np.asarray(load_time_s, dtype=float)
    load_demand_mw = np.asarray(load_demand_mw, dtype=float)
    if load_time_s.shape != load_demand_mw.shape:
        raise ValueError("load_time_s and load_demand_mw must be the same shape")
    if not np.all(np.diff(load_time_s) > 0):
        raise ValueError("load_time_s must be strictly increasing")

    Sn = params.Sn_mva
    load_pu = load_demand_mw / Sn

    # Extend the start to t = 0 (use the first sample as the preamble).
    if load_time_s[0] > 0:
        load_time_s = np.concatenate([[0.0], load_time_s])
        load_pu = np.concatenate([[load_pu[0]], load_pu])

    # ---- Initial condition: steady state at the first load point ----
    Pe0 = float(load_pu[0])
    if Pe0 > params.vmax_pu:
        raise ValueError(
            f"Initial load {Pe0 * Sn:.2f} MW exceeds turbine thermal rating "
            f"{params.vmax_pu * Sn:.2f} MW. Refusing to start above VMAX."
        )
    Pref = Pe0  # governor reference frozen at initial steady-state
    state = TGOV1State(
        delta=0.0, omega=1.0,
        x1=Pe0, P_valve=Pe0, P_fuel=Pe0, Pm=Pe0,
    )

    # ---- Outer event-driven loop [A6] ----
    if params.chunked:
        t_eval = np.arange(0.0, load_time_s[-1] + sample_dt_s, sample_dt_s)
        y_eval = np.empty((6, t_eval.size), dtype=float)
        y_eval[:, 0] = state.as_array()
        eval_idx = 1

        for k in range(load_time_s.size - 1):
            t0 = float(load_time_s[k])
            t1 = float(load_time_s[k + 1])
            Pe_chunk = float(load_pu[k])

            rhs = _rhs_factory(params, Pref, Pe_chunk)
            sol = solve_ivp(
                rhs, (t0, t1), state.as_array(),
                method="RK45",
                max_step=params.max_step_s,
                rtol=params.rtol, atol=params.atol,
                dense_output=True,
            )
            if not sol.success:
                raise RuntimeError(f"solve_ivp failed in [{t0}, {t1}]: {sol.message}")

            # Sample any t_eval points that lie strictly within this chunk
            while eval_idx < t_eval.size and t_eval[eval_idx] <= t1 + 1e-9:
                y_eval[:, eval_idx] = sol.sol(t_eval[eval_idx])
                eval_idx += 1

            # Pass the final state as the next chunk's IC
            state = TGOV1State.from_array(sol.y[:, -1])

        # Pad any trailing eval points
        while eval_idx < t_eval.size:
            y_eval[:, eval_idx] = sol.sol(t_eval[eval_idx])
            eval_idx += 1
    else:
        # ---- Baseline path: one solve with ZOH interpolation ----
        from scipy.interpolate import interp1d
        Pe_interp = interp1d(
            load_time_s, load_pu, kind="zero",
            bounds_error=False, fill_value=(load_pu[0], load_pu[-1]),
        )

        def rhs_baseline(t: float, y: np.ndarray) -> np.ndarray:
            return _rhs_factory(params, Pref, float(Pe_interp(t)))(t, y)

        t_eval = np.arange(0.0, load_time_s[-1] + sample_dt_s, sample_dt_s)
        sol = solve_ivp(
            rhs_baseline, (0.0, float(load_time_s[-1])), state.as_array(),
            method="RK45",
            max_step=params.max_step_s,
            rtol=params.rtol, atol=params.atol,
            dense_output=True,
        )
        if not sol.success:
            raise RuntimeError(f"baseline solve_ivp failed: {sol.message}")
        y_eval = sol.sol(t_eval)

    # ---- Post-process into physical units ----
    delta_rad = y_eval[0]
    omega_pu = y_eval[1]
    x1_pu = y_eval[2]
    P_valve_pu = y_eval[3]
    P_fuel_pu = y_eval[4]
    Pm_pu = y_eval[5]

    freq_hz = omega_pu * 60.0
    speed_rpm = freq_hz * 60.0
    Pm_mw = Pm_pu * Sn

    # Build Pe(t) on the eval grid (ZOH) for diagnostics
    Pe_mw = np.empty_like(t_eval)
    k = 0
    for i, t in enumerate(t_eval):
        while k + 1 < load_time_s.size and load_time_s[k + 1] <= t:
            k += 1
        Pe_mw[i] = load_pu[k] * Sn
    Pe_pu = Pe_mw / Sn

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
        P_valve_pu=P_valve_pu,
        P_fuel_pu=P_fuel_pu,
        x1_pu=x1_pu,
    )

    # ---- [A5] Fuel from the valve, not Pm ----
    if dispatch_fn is not None:
        fuel_signal_pu = P_fuel_pu if params.fuel_from_valve else Pm_pu
        # Convert to load fraction on the turbine's surrogate base.
        # P_turbine_mw is the surrogate's rated power.
        load_frac = np.clip(fuel_signal_pu * Sn / params.P_turbine_mw, 0.0, 1.0)
        disp = dispatch_fn(load_frac)
        result.fuel_kg_s = np.asarray(disp["fuel_kg_s"], dtype=float)
        result.cum_fuel_kg = np.concatenate(
            [[0.0],
             np.cumsum(0.5 * (result.fuel_kg_s[1:] + result.fuel_kg_s[:-1])
                       * np.diff(t_eval))]
        )

    return result

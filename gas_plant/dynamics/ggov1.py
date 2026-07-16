"""Tier B: GGOV1 governor + swing equation for the LM2500.

Implements the IEEE PES-TR1 (2013) Fig 3-5 GGOV1 gas-turbine governor model
in pure scipy. Replaces TGOV1 (Tier A) with a structure that captures the
three principal LM2500 control loops:

  - Speed/power governor (PI on speed error + electrical-power droop feedback)
  - Acceleration controller (integral on filtered angular acceleration)
  - Temperature/load limiter (PI on a fuel-flow-derived temperature proxy)

Arbitrated through a low-value-select (LVG) on the three outputs `fsrn`,
`fsra`, `fsrt`. Back-calculation anti-windup keeps the two unselected
controllers' integrators tracking the actual valve command so they re-engage
cleanly when conditions cross.

Default parameter set comes from PES-TR1 Appendix C (typical GE GGOV1 values).
LM2500-specific overrides are documented in `docs/tier_plan.md` Tier B section
and live in `GGOV1Params.lm2500_overrides()`.

Tier-A improvements that survive into Tier B:
  A3  Frequency-dependent load damping: Pe(t,omega) = Pe_load*(1+alpha*(omega-1))
  A5  Fuel reported to dispatch_fn from a SEPARATE combustor lag, not Pm
  A6  Event-driven (chunked) integration across Load17 sample boundaries

State vector (12 states):
    delta        rotor angle (rad)
    omega        rotor speed, per-unit on omega_0 = 2*pi*60
    Pe_filt      Pe through Tpelec lag (pu, gen base)
    x_kigov      speed PI integrator state
    x_ka         acceleration-controller integrator (= fsra signal)
    x_accel_lag  speed lag for d/dt filter (Ta block)
    x_kiload     temperature PI integrator state
    x_tload      temperature signal lag (Tfload block)
    x_tsab       temperature signal lead-lag (Tsa/Tsb block)
    valve        fuel-valve position (pu), after Tact + rate-limit
    x_turb       turbine lead-lag state (Tb/Tc on fuel)
    P_fuel       slow combustor lag for fuel reporting (Tier A A5)
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
class GGOV1Params:
    """GGOV1 parameters. Defaults from IEEE PES-TR1 (2013) Appendix C.

    All per-unit values are on the GENERATOR MVA base `Sn_mva` for consistency
    with Tier A swing equation. PES-TR1 §3.3 recommends putting the governor on
    turbine MW base; for the LM2500 (22 MW turbine / 23 MVA gen) the conversion
    factor is only 22/23 ≈ 0.957, applied via VMAX rebase (see Tier A A4) and
    via Kturb. Document deviations in docs/tier_plan.md.
    """

    # ----- Machine / swing equation (per Sn_mva) -----
    Sn_mva: float = 23.0
    H_s: float = 2.8
    D_pu: float = 1.0

    # ----- Turbine thermal rating -----
    P_turbine_mw: float = 22.0   # LM2500 base; used for VMAX rebase + fuel callback

    # ----- GGOV1 speed/power governor -----
    R: float = 0.04              # droop (pu)
    rselect: int = 1             # 1=electrical power, -1=valve stroke, -2=gov out, 0=isochronous
    Tpelec_s: float = 1.0        # electrical-power transducer lag
    MaxERR: float = 0.05         # speed-error upper clamp
    MinERR: float = -0.05        # speed-error lower clamp
    Kpgov: float = 10.0          # PI proportional
    Kigov: float = 2.0           # PI integral

    # ----- Acceleration controller -----
    aset_pu_s: float = 0.01      # acceleration setpoint (GE GT)
    Ka: float = 10.0             # accel controller gain
    Ta_s: float = 0.1            # speed-derivative filter

    # ----- Temperature / load limiter -----
    Ldref: float = 1.0           # load reference (max output, pu turbine MW base)
    Kpload: float = 2.0          # temp PI proportional
    Kiload: float = 0.67         # temp PI integral
    Tfload_s: float = 3.0        # temperature filter lag
    Tsa_s: float = 4.0           # temperature signal lead
    Tsb_s: float = 5.0           # temperature signal lag

    # ----- Valve actuator -----
    Tact_s: float = 0.5          # actuator time constant (PES-TR1 default; LM2500 likely faster)
    Vmax_pu: float = 22.0 / 23.0  # rebased to turbine MW on gen base (Tier A A4)
    Vmin_pu: float = 0.15        # lean-blowout floor (matches Tier A)
    Ropen_pu_s: float = 0.10     # valve open rate limit
    Rclose_pu_s: float = -0.10   # valve close rate limit

    # ----- Turbine block -----
    Kturb: float = 1.5           # turbine gain (per PES-TR1; Pm = Kturb*(Wf - wfnl))
    Wfnl: float = 0.2            # full-speed-no-load fuel
    Tb_s: float = 0.1            # turbine lag
    Tc_s: float = 0.0            # turbine lead (0 disables lead-lag)
    Teng_s: float = 0.0          # engine transport delay (always 0 for GT)
    flag: int = 1                # 1=fuel proportional to speed (shaft-driven pump)

    # ----- Damping in turbine output (DM) -----
    Dm: float = 0.0              # default 0 = no speed-dependent turbine damping

    # ----- Inherited Tier-A behaviors -----
    alpha_load_damping: float = 1.5   # A3
    T_comb_s: float = 0.3             # A5 (fuel-reporting lag)
    chunked: bool = True              # A6

    # ----- Anti-windup tracking gain (back-calculation) -----
    # ALWAYS-active back-calculation form:
    #   dx_kigov/dt = Kigov*err_speed - Kbc_speed * (fsrn - fsr)
    # When the controller IS selected, (fsrn - fsr) = 0 so no extra dynamics.
    # When NOT selected, (fsrn - fsr) > 0 and the term pulls the integrator
    # down to track fsr. This formulation is smooth (no if/else on selection),
    # which is critical for the ODE solver to take reasonable step sizes.
    Kbc_speed: float = 100.0   # back-calc gain for x_kigov
    Kbc_accel: float = 100.0   # back-calc gain for x_ka
    Kbc_temp: float = 50.0     # back-calc gain for x_kiload

    # ----- Integrator -----
    # rtol=1e-6 atol=1e-8 needed because omega ~ 1.0 and small relative errors
    # accumulate into large frequency drift over long simulations.
    rtol: float = 1e-6
    atol: float = 1e-8
    max_step_s: float = 0.5

    # ----- Derived (cached) -----
    omega_0_rad_s: float = field(default=2.0 * np.pi * 60.0, init=False)
    M_pu_s: float = field(init=False)

    def __post_init__(self) -> None:
        self.M_pu_s = 2.0 * self.H_s

    @classmethod
    def lm2500_overrides(cls, **extra) -> "GGOV1Params":
        """Construct with LM2500-specific overrides on top of PES-TR1 defaults.

        Justifications in docs/tier_plan.md, Tier B section.
        """
        defaults = dict(
            Tact_s=0.15,         # Woodward MkVIe/NetCon 5000 is faster than heavy-duty hydraulic
            Vmax_pu=22.0 / 23.0, # turbine MW on gen base (Tier A A4)
            Vmin_pu=0.15,        # lean-blowout floor
            R=0.04,              # 4% droop aero islanded
            # PES-TR1 defaults retained: Kpgov=10, Kigov=2, Kturb=1.5, Wfnl=0.2, Tb=0.1
            # PES-TR1 defaults retained for temp limiter: Kpload=2, Kiload=0.67, Tfload=3, Tsa=4, Tsb=5
            # PES-TR1 defaults retained for accel ctrl: aset=0.01, Ka=10, Ta=0.1
        )
        defaults.update(extra)
        return cls(**defaults)


@dataclass
class GGOV1State:
    """Initial / instantaneous ODE state for the 12-state GGOV1 model."""

    delta: float = 0.0
    omega: float = 1.0
    Pe_filt: float = 0.0
    x_kigov: float = 0.0
    x_ka: float = 0.0
    x_accel_lag: float = 1.0      # init to omega so derivative starts at 0
    x_kiload: float = 0.0
    x_tload: float = 0.0
    x_tsab: float = 0.0
    valve: float = 0.0
    x_turb: float = 0.0
    P_fuel: float = 0.0

    def as_array(self) -> np.ndarray:
        return np.array([self.delta, self.omega, self.Pe_filt, self.x_kigov,
                         self.x_ka, self.x_accel_lag, self.x_kiload,
                         self.x_tload, self.x_tsab, self.valve, self.x_turb,
                         self.P_fuel], dtype=float)

    @classmethod
    def from_array(cls, y: np.ndarray) -> "GGOV1State":
        return cls(delta=float(y[0]), omega=float(y[1]), Pe_filt=float(y[2]),
                   x_kigov=float(y[3]), x_ka=float(y[4]),
                   x_accel_lag=float(y[5]), x_kiload=float(y[6]),
                   x_tload=float(y[7]), x_tsab=float(y[8]),
                   valve=float(y[9]), x_turb=float(y[10]), P_fuel=float(y[11]))


@dataclass
class GGOV1Result:
    """Time-resolved GGOV1 simulation, drop-in compatible with TGOV1Result."""

    t_s: np.ndarray
    delta_rad: np.ndarray
    omega_pu: np.ndarray
    freq_hz: np.ndarray
    speed_rpm: np.ndarray
    Pm_mw: np.ndarray
    Pm_pu: np.ndarray
    Pe_mw: np.ndarray
    Pe_pu: np.ndarray
    valve_pu: np.ndarray
    P_fuel_pu: np.ndarray
    # Diagnostics: which controller is in command at each sample
    fsrn_pu: np.ndarray  # speed governor would-be output
    fsra_pu: np.ndarray  # acceleration controller would-be output
    fsrt_pu: np.ndarray  # temperature limiter would-be output
    fsr_pu: np.ndarray   # LVG-selected (= min)
    fuel_kg_s: Optional[np.ndarray] = None
    cum_fuel_kg: Optional[np.ndarray] = None

    def as_dataframe(self) -> pd.DataFrame:
        cols = {
            "t_s": self.t_s, "delta_rad": self.delta_rad,
            "omega_pu": self.omega_pu, "freq_hz": self.freq_hz,
            "speed_rpm": self.speed_rpm,
            "Pm_mw": self.Pm_mw, "Pm_pu": self.Pm_pu,
            "Pe_mw": self.Pe_mw, "Pe_pu": self.Pe_pu,
            "valve_pu": self.valve_pu, "P_fuel_pu": self.P_fuel_pu,
            "fsrn_pu": self.fsrn_pu, "fsra_pu": self.fsra_pu,
            "fsrt_pu": self.fsrt_pu, "fsr_pu": self.fsr_pu,
        }
        if self.fuel_kg_s is not None:
            cols["fuel_kg_s"] = self.fuel_kg_s
        if self.cum_fuel_kg is not None:
            cols["cum_fuel_kg"] = self.cum_fuel_kg
        return pd.DataFrame(cols)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_controllers(omega, Pe_filt, Pref, valve, x_kigov, x_kiload,
                         x_tload, x_accel_lag, x_ka, p):
    """Compute the three would-be controller outputs (fsrn, fsra, fsrt).

    These are 'unconstrained' values — what each controller WOULD command
    if it were the one selected by the LVG. The smallest one wins.
    """
    # ----- Speed governor (PI on speed-equivalent error including droop) -----
    # Standard droop form: shifts speed setpoint by R*(Pe - Pref).
    err_speed = (1.0 - omega) + p.R * (Pref - Pe_filt)
    err_speed = float(np.clip(err_speed, p.MinERR, p.MaxERR))
    fsrn = p.Kpgov * err_speed + x_kigov

    # ----- Acceleration controller (integral on aset error) -----
    accel = (omega - x_accel_lag) / p.Ta_s if p.Ta_s > 0 else 0.0
    err_accel = p.aset_pu_s - accel
    fsra = x_ka  # pure integrator output

    # ----- Temperature limiter (PI on fuel-derived temperature error) -----
    tlim = p.Ldref / p.Kturb + p.Wfnl   # max-load fuel reference
    err_temp = tlim - x_tload            # positive when below limit
    fsrt = p.Kpload * err_temp + x_kiload

    return fsrn, fsra, fsrt, err_speed, err_accel, err_temp


def _rhs_factory(params: GGOV1Params, Pref: float, Pe_load_pu: float):
    """Build a chunked-RHS closure for a constant Pe_load segment."""
    p = params

    def rhs(t: float, y: np.ndarray) -> np.ndarray:
        (delta, omega, Pe_filt, x_kigov, x_ka, x_accel_lag, x_kiload,
         x_tload, x_tsab, valve, x_turb, P_fuel) = y

        # --- Electrical power with Tier-A load damping ---
        Pe = Pe_load_pu * (1.0 + p.alpha_load_damping * (omega - 1.0))

        # --- Power transducer (Pe -> Pe_filt) ---
        if p.Tpelec_s > 0:
            dPe_filt = (Pe - Pe_filt) / p.Tpelec_s
        else:
            Pe_filt = Pe
            dPe_filt = 0.0

        # --- Controllers ---
        fsrn, fsra, fsrt, err_speed, err_accel, err_temp = _compute_controllers(
            omega, Pe_filt, Pref, valve, x_kigov, x_kiload, x_tload,
            x_accel_lag, x_ka, p,
        )

        # --- LVG: lowest signal wins (PES-TR1 §3.1.2.3) ---
        fsr = min(fsrn, fsra, fsrt)

        # --- Always-active back-calculation anti-windup ---
        # The term (fsrX - fsr) is >= 0 by definition of fsr=min(...).
        # When fsrX IS selected, (fsrX - fsr) = 0, so no extra dynamics: pure PI.
        # When fsrX is NOT selected, (fsrX - fsr) > 0, pulling the integrator
        # toward the actual valve command. This is C0-smooth — no if/else
        # branches that would cause ODE-solver step rejection.
        dx_kigov  = p.Kigov  * err_speed - p.Kbc_speed * (fsrn - fsr)
        dx_ka     = p.Ka     * err_accel - p.Kbc_accel * (fsra - fsr)
        dx_kiload = p.Kiload * err_temp  - p.Kbc_temp  * (fsrt - fsr)

        # Acceleration speed-lag (for derivative filter)
        if p.Ta_s > 0:
            dx_accel_lag = (omega - x_accel_lag) / p.Ta_s
        else:
            dx_accel_lag = 0.0

        # --- Valve actuator (Tact + rate limit + Vmin/Vmax clamp) ---
        # Direct rate-limited first-order chase toward fsr.
        valve_target = float(np.clip(fsr, p.Vmin_pu, p.Vmax_pu))
        raw_rate = (valve_target - valve) / max(p.Tact_s, 1e-6)
        rate = float(np.clip(raw_rate, p.Rclose_pu_s, p.Ropen_pu_s))
        # If valve is at a limit and rate would push it further: zero rate.
        if (valve >= p.Vmax_pu and rate > 0) or (valve <= p.Vmin_pu and rate < 0):
            rate = 0.0
        dvalve = rate

        # --- Turbine block: Wf -> lead-lag -> Pmech ---
        # Wf = valve * (omega if flag=1 else 1) — shaft-driven fuel pump
        Wf = valve * (omega if p.flag == 1 else 1.0)
        # Lead-lag (1+sTc)/(1+sTb): for Tc=0 it's just lag 1/(1+sTb)
        if p.Tb_s > 0:
            dx_turb = (Wf - x_turb) / p.Tb_s
        else:
            dx_turb = 0.0
            x_turb = Wf
        # Pmech = Kturb * (x_turb_out - Wfnl) ; x_turb_out = x_turb + Tc*dx_turb
        x_turb_out = x_turb + p.Tc_s * dx_turb
        Pmech = p.Kturb * (x_turb_out - p.Wfnl) + p.Dm * (omega - 1.0)

        # --- Swing equation ---
        domega = (Pmech - Pe - p.D_pu * (omega - 1.0)) / p.M_pu_s
        ddelta = p.omega_0_rad_s * (omega - 1.0)

        # --- Temperature signal path (lead-lag then lag) ---
        # tex proxy = Pmech (per PES-TR1 §3.1.2.3 simplification)
        tex = Pmech / p.Kturb + p.Wfnl  # back-derive the "fuel-equivalent" temperature signal
        # Lead-lag (1+sTsa)/(1+sTsb)
        if p.Tsb_s > 0:
            dx_tsab = (tex - x_tsab) / p.Tsb_s
        else:
            dx_tsab = 0.0
        tsab_out = x_tsab + p.Tsa_s * dx_tsab
        # Lag 1/(1+sTfload)
        if p.Tfload_s > 0:
            dx_tload = (tsab_out - x_tload) / p.Tfload_s
        else:
            dx_tload = 0.0

        # --- Fuel reporting lag (Tier A A5) ---
        dP_fuel = (valve - P_fuel) / p.T_comb_s

        return np.array([ddelta, domega, dPe_filt, dx_kigov, dx_ka,
                         dx_accel_lag, dx_kiload, dx_tload, dx_tsab,
                         dvalve, dx_turb, dP_fuel])

    return rhs


# ---------------------------------------------------------------------------
# Initial-condition solver
# ---------------------------------------------------------------------------

def _initial_state_for_load(Pe0_pu: float, p: GGOV1Params) -> GGOV1State:
    """Find a self-consistent steady-state IC at Pe0_pu (gen base).

    With the always-active back-calculation form (Kbc terms), in steady state:
      speed loop:  fsrn = fsr     -> x_kigov = fsr (since err_speed = 0)
      accel loop:  fsra - fsr = Ka*aset / Kbc_accel  (small positive offset)
      temp loop:   fsrt - fsr = Kiload*err_temp / Kbc_temp
      valve == fsr (since Tact actuator at SS gives valve = fsr)
      Pm = Kturb*(valve - Wfnl) = Pe0  =>  valve_ss = Pe0/Kturb + Wfnl
    """
    valve_ss = (Pe0_pu / p.Kturb + p.Wfnl) / 1.0  # omega=1
    fsr_ss = valve_ss

    # Steady-state offsets from back-calculation balance
    fsra_offset = p.Ka * p.aset_pu_s / p.Kbc_accel
    err_temp_ss = (p.Ldref / p.Kturb + p.Wfnl) - valve_ss
    fsrt_offset = p.Kiload * err_temp_ss / p.Kbc_temp

    x_ka_ss = fsr_ss + fsra_offset
    x_kiload_ss = (fsr_ss + fsrt_offset) - p.Kpload * err_temp_ss

    return GGOV1State(
        delta=0.0,
        omega=1.0,
        Pe_filt=Pe0_pu,
        x_kigov=fsr_ss,
        x_ka=x_ka_ss,
        x_accel_lag=1.0,
        x_kiload=x_kiload_ss,
        x_tload=valve_ss,
        x_tsab=valve_ss,
        valve=valve_ss,
        x_turb=valve_ss,
        P_fuel=valve_ss,
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def simulate_ggov1(
    load_time_s: np.ndarray,
    load_demand_mw: np.ndarray,
    params: GGOV1Params = GGOV1Params(),
    sample_dt_s: float = 1.0,
    dispatch_fn: Optional[Callable[[np.ndarray], dict]] = None,
    initial_state: Optional[GGOV1State] = None,
) -> GGOV1Result:
    """Run GGOV1 over a Load17-style step profile."""
    load_time_s = np.asarray(load_time_s, dtype=float)
    load_demand_mw = np.asarray(load_demand_mw, dtype=float)
    if load_time_s.shape != load_demand_mw.shape:
        raise ValueError("load_time_s and load_demand_mw must be the same shape")
    if not np.all(np.diff(load_time_s) > 0):
        raise ValueError("load_time_s must be strictly increasing")

    Sn = params.Sn_mva
    load_pu = load_demand_mw / Sn

    if load_time_s[0] > 0:
        load_time_s = np.concatenate([[0.0], load_time_s])
        load_pu = np.concatenate([[load_pu[0]], load_pu])

    Pe0 = float(load_pu[0])
    if Pe0 > params.Vmax_pu * params.Kturb * 1.05:  # 5% slack
        raise ValueError(
            f"Initial load {Pe0 * Sn:.2f} MW exceeds turbine envelope; "
            f"reduce or rebase Vmax."
        )
    Pref = Pe0
    state = initial_state if initial_state is not None else _initial_state_for_load(Pe0, params)

    t_eval = np.arange(0.0, load_time_s[-1] + sample_dt_s, sample_dt_s)
    y_eval = np.empty((12, t_eval.size), dtype=float)
    y_eval[:, 0] = state.as_array()
    eval_idx = 1

    if params.chunked:
        for k in range(load_time_s.size - 1):
            t0 = float(load_time_s[k])
            t1 = float(load_time_s[k + 1])
            Pe_chunk = float(load_pu[k])
            rhs = _rhs_factory(params, Pref, Pe_chunk)
            sol = solve_ivp(
                rhs, (t0, t1), state.as_array(),
                method="RK45",   # smooth LVG via back-calc => RK45 handles it
                max_step=params.max_step_s,
                rtol=params.rtol, atol=params.atol,
                dense_output=True,
            )
            if not sol.success:
                raise RuntimeError(f"solve_ivp failed in [{t0}, {t1}]: {sol.message}")
            while eval_idx < t_eval.size and t_eval[eval_idx] <= t1 + 1e-9:
                y_eval[:, eval_idx] = sol.sol(t_eval[eval_idx])
                eval_idx += 1
            state = GGOV1State.from_array(sol.y[:, -1])
        while eval_idx < t_eval.size:
            y_eval[:, eval_idx] = sol.sol(t_eval[eval_idx])
            eval_idx += 1
    else:
        # Non-chunked: ZOH interpolant via simple stepping
        from scipy.interpolate import interp1d
        Pe_interp = interp1d(load_time_s, load_pu, kind="zero",
                             bounds_error=False, fill_value=(load_pu[0], load_pu[-1]))
        def rhs_z(t, y):
            return _rhs_factory(params, Pref, float(Pe_interp(t)))(t, y)
        sol = solve_ivp(rhs_z, (0.0, float(load_time_s[-1])), state.as_array(),
                        method="RK45", max_step=params.max_step_s,
                        rtol=params.rtol, atol=params.atol, dense_output=True)
        if not sol.success:
            raise RuntimeError(f"baseline solve_ivp failed: {sol.message}")
        y_eval = sol.sol(t_eval)

    # ---- Post-process ----
    delta = y_eval[0]
    omega = y_eval[1]
    Pe_filt = y_eval[2]
    x_kigov = y_eval[3]
    x_ka = y_eval[4]
    x_accel_lag = y_eval[5]
    x_kiload = y_eval[6]
    x_tload = y_eval[7]
    x_tsab = y_eval[8]
    valve = y_eval[9]
    x_turb = y_eval[10]
    P_fuel = y_eval[11]

    freq_hz = omega * 60.0
    speed_rpm = freq_hz * 60.0

    # Pmech from final state
    Wf = valve * np.where(params.flag == 1, omega, 1.0)
    dx_turb = (Wf - x_turb) / params.Tb_s if params.Tb_s > 0 else 0.0
    x_turb_out = x_turb + params.Tc_s * dx_turb
    Pm_pu = params.Kturb * (x_turb_out - params.Wfnl) + params.Dm * (omega - 1.0)
    Pm_mw = Pm_pu * Sn

    # Pe(t) on the eval grid (ZOH)
    Pe_mw = np.empty_like(t_eval)
    k = 0
    for i, t in enumerate(t_eval):
        while k + 1 < load_time_s.size and load_time_s[k + 1] <= t:
            k += 1
        Pe_mw[i] = load_pu[k] * Sn
    Pe_pu = Pe_mw / Sn

    # Re-compute controller signals for diagnostics
    fsrn = np.empty_like(t_eval)
    fsra = np.empty_like(t_eval)
    fsrt = np.empty_like(t_eval)
    for i in range(t_eval.size):
        n, a, t, _, _, _ = _compute_controllers(
            omega[i], Pe_filt[i], Pref, valve[i], x_kigov[i], x_kiload[i],
            x_tload[i], x_accel_lag[i], x_ka[i], params,
        )
        fsrn[i], fsra[i], fsrt[i] = n, a, t
    fsr = np.minimum(np.minimum(fsrn, fsra), fsrt)

    result = GGOV1Result(
        t_s=t_eval, delta_rad=delta, omega_pu=omega,
        freq_hz=freq_hz, speed_rpm=speed_rpm,
        Pm_mw=Pm_mw, Pm_pu=Pm_pu, Pe_mw=Pe_mw, Pe_pu=Pe_pu,
        valve_pu=valve, P_fuel_pu=P_fuel,
        fsrn_pu=fsrn, fsra_pu=fsra, fsrt_pu=fsrt, fsr_pu=fsr,
    )

    # ---- Fuel via dispatch_fn (Tier A A5: fuel from P_fuel, not Pm) ----
    if dispatch_fn is not None:
        load_frac = np.clip(P_fuel * Sn / params.P_turbine_mw, 0.0, 1.0)
        disp = dispatch_fn(load_frac)
        result.fuel_kg_s = np.asarray(disp["fuel_kg_s"], dtype=float)
        result.cum_fuel_kg = np.concatenate(
            [[0.0],
             np.cumsum(0.5 * (result.fuel_kg_s[1:] + result.fuel_kg_s[:-1])
                       * np.diff(t_eval))]
        )

    return result


# ---------------------------------------------------------------------------
# Convenience: step-response driver for Hannett-style validation
# ---------------------------------------------------------------------------

def step_response(
    P_initial_mw: float,
    P_final_mw: float,
    t_step_s: float = 1.0,
    t_end_s: float = 20.0,
    sample_dt_s: float = 0.01,
    params: Optional[GGOV1Params] = None,
) -> GGOV1Result:
    """Drive a step change in electrical demand from P_initial → P_final at t=t_step.

    Used for Hannett-style validation against Table 3 "time-to-60%-Pm" and
    Figs 4/5 (load-rejection) overlays.
    """
    if params is None:
        params = GGOV1Params.lm2500_overrides()
    # Build a 2-sample profile: P_initial held until t_step, then P_final
    load_time_s = np.array([0.0, t_step_s, t_end_s])
    load_demand_mw = np.array([P_initial_mw, P_final_mw, P_final_mw])
    return simulate_ggov1(
        load_time_s=load_time_s,
        load_demand_mw=load_demand_mw,
        params=params,
        sample_dt_s=sample_dt_s,
    )

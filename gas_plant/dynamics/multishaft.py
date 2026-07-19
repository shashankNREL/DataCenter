"""Tier C (scipy): Two-mass mechanical shaft + voltage-sensitive load + GGOV1.

Adds to Tier B's GGOV1:

  - **Two-mass mechanical shaft model**: HP rotor (gas generator, ~9500 rpm
    at full power) and PT/generator rotor (3600 rpm, direct-coupled). The
    HP rotor has its own swing equation driven by governor turbine power
    minus the gas-path coupling power delivered to the PT rotor.

  - **Voltage-sensitive load via ZIP exponent**: Pe(t,w,V) = demand(t) *
    (V/Vref)^pv * (1 + alpha*(w-1)). V is held at 1 pu here (perfect AVR
    assumption); use the ANDES path for V-Q dynamics.

PER-UNIT SYSTEM (V&V Phase 0, fix G1): governor and gas-path signals are pu
on the TURBINE base `Trate_mw`; the PT/gen swing equation is on the
GENERATOR base `Sn_mva`; conversion factor kb = Trate/Sn at the interface.
The HP-rotor swing runs entirely on the turbine base (its power balance is
governor power vs gas-path coupling power, both turbine-base signals) with
inertia H_hp interpreted on that base.

GAS-PATH COUPLING (documented approximation — see docs/constants.md):
P_couple = K_couple * (omega_hp - omega_hp_idle), linear between idle NGG
(4900 rpm) and full NGG (9500 rpm). Real FPT power vs NGG speed is strongly
nonlinear (~cubic) and depends on PT speed; this linear map is a placeholder
pending a spool-up trace to calibrate against. Status: PLACEHOLDER.

State vector (13 states = 12 GGOV1 + omega_hp).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import pandas as pd
from scipy.integrate import solve_ivp

from .ggov1 import (
    GGOV1Params,
    _compute_controllers,
    _turbine_power,
    _pref_for_rselect,
)


# ---------------------------------------------------------------------------
# Parameters and state
# ---------------------------------------------------------------------------

@dataclass
class MultishaftParams:
    """Tier C parameters: GGOV1 + two-mass shaft + ZIP-exponent load."""

    ggov1: GGOV1Params = field(default_factory=GGOV1Params.lm2500_overrides)

    # ----- Two-mass split -----
    # Total LM2500 system H split between HP rotor (light, fast) and
    # PT/generator rotor (heavy, slow). Status: ESTIMATED (docs/constants.md).
    H_pt_s: float = 2.5      # PT + generator rotor inertia (s, gen base)
    H_hp_s: float = 0.3      # HP rotor inertia (s, turbine base)
    D_pt: float = 0.0        # PT/gen rotor damping — 0 per V&V fix G5
    # D_hp = 0: the HP rotor's natural speed is the gas-path operating
    # point, not 60 Hz; a D*(omega-1) term would wrongly pull it to 60 Hz.
    D_hp: float = 0.0

    # Coupling: P_couple(pu turbine) = K_couple * (omega_hp - omega_hp_idle)
    omega_hp_idle: float = 0.516   # 4900/9500 rpm (Pocket Guide Table 1-2)
    K_couple: float = field(init=False)

    # ----- ZIP voltage exponent on Pe -----
    pv_exponent: float = 0.0
    V_term_pu: float = 1.0
    V_ref_pu: float = 1.0

    # ----- Integrator -----
    rtol: float = 1e-6
    atol: float = 1e-8
    max_step_s: float = 0.5

    # ----- Derived -----
    M_pt: float = field(init=False)
    M_hp: float = field(init=False)
    omega_0_rad_s: float = field(default=2.0 * np.pi * 60.0, init=False)

    def __post_init__(self) -> None:
        self.M_pt = 2.0 * self.H_pt_s
        self.M_hp = 2.0 * self.H_hp_s
        # K_couple normalized so P_couple(omega_hp=1) = 1.0 pu turbine
        self.K_couple = 1.0 / max(1.0 - self.omega_hp_idle, 1e-6)


@dataclass
class MultishaftState:
    delta_pt: float = 0.0
    omega_pt: float = 1.0
    Pe_filt: float = 0.0        # turbine-base pu
    x_kigov: float = 0.0
    x_ka: float = 0.0
    x_accel_lag: float = 1.0
    x_kiload: float = 0.0
    x_tload: float = 0.0
    x_tsab: float = 0.0
    valve: float = 0.0
    x_turb: float = 0.0
    P_fuel: float = 0.0
    omega_hp: float = 1.0

    def as_array(self) -> np.ndarray:
        return np.array([
            self.delta_pt, self.omega_pt, self.Pe_filt, self.x_kigov,
            self.x_ka, self.x_accel_lag, self.x_kiload, self.x_tload,
            self.x_tsab, self.valve, self.x_turb, self.P_fuel,
            self.omega_hp,
        ], dtype=float)

    @classmethod
    def from_array(cls, y: np.ndarray) -> "MultishaftState":
        return cls(
            delta_pt=float(y[0]), omega_pt=float(y[1]), Pe_filt=float(y[2]),
            x_kigov=float(y[3]), x_ka=float(y[4]), x_accel_lag=float(y[5]),
            x_kiload=float(y[6]), x_tload=float(y[7]), x_tsab=float(y[8]),
            valve=float(y[9]), x_turb=float(y[10]), P_fuel=float(y[11]),
            omega_hp=float(y[12]),
        )


@dataclass
class MultishaftResult:
    t_s: np.ndarray
    delta_pt_rad: np.ndarray
    omega_pt_pu: np.ndarray
    freq_hz: np.ndarray
    Pe_mw: np.ndarray            # actual, incl. damping + V exponent
    Pe_demand_mw: np.ndarray
    omega_hp_pu: np.ndarray
    speed_hp_rpm: np.ndarray
    Pm_pt_mw: np.ndarray         # power delivered to PT via gas path
    Pm_hp_mw: np.ndarray         # governor turbine power into HP rotor
    P_couple_mw: np.ndarray
    valve_pu: np.ndarray         # turbine-base pu
    P_fuel_pu: np.ndarray
    fuel_kg_s: Optional[np.ndarray] = None
    cum_fuel_kg: Optional[np.ndarray] = None

    def as_dataframe(self) -> pd.DataFrame:
        d = {
            "t_s": self.t_s,
            "delta_pt_rad": self.delta_pt_rad,
            "omega_pt_pu": self.omega_pt_pu,
            "freq_hz": self.freq_hz,
            "Pe_mw": self.Pe_mw,
            "Pe_demand_mw": self.Pe_demand_mw,
            "omega_hp_pu": self.omega_hp_pu,
            "speed_hp_rpm": self.speed_hp_rpm,
            "Pm_pt_mw": self.Pm_pt_mw,
            "Pm_hp_mw": self.Pm_hp_mw,
            "P_couple_mw": self.P_couple_mw,
            "valve_pu": self.valve_pu,
            "P_fuel_pu": self.P_fuel_pu,
        }
        if self.fuel_kg_s is not None:
            d["fuel_kg_s"] = self.fuel_kg_s
        if self.cum_fuel_kg is not None:
            d["cum_fuel_kg"] = self.cum_fuel_kg
        return pd.DataFrame(d)


# ---------------------------------------------------------------------------
# RHS
# ---------------------------------------------------------------------------

def _rhs_factory(params: MultishaftParams, Pref: float, P_demand_gen_pu: float):
    """Build chunked RHS for a constant Pe_demand segment (gen-base pu)."""
    p = params
    g = p.ggov1

    def rhs(t: float, y: np.ndarray) -> np.ndarray:
        st = MultishaftState.from_array(y)

        # ---- Pe with frequency damping + voltage exponent (gen base) ----
        V_ratio = p.V_term_pu / p.V_ref_pu
        Pe_gen = (P_demand_gen_pu * (V_ratio ** p.pv_exponent)
                  * (1.0 + g.alpha_load_damping * (st.omega_pt - 1.0)))
        Pe_turb = Pe_gen / g.kb

        # ---- Power transducer (turbine base) ----
        if g.Tpelec_s > 0:
            dPe_filt = (Pe_turb - st.Pe_filt) / g.Tpelec_s
        else:
            dPe_filt = 0.0

        # ---- GGOV1 controllers (speed feedback = PT rotor speed: the
        #      governor regulates the power-turbine shaft at 3600 rpm) ----
        fsrn, fsra, fsrt, err_speed, err_accel, err_temp = _compute_controllers(
            st.omega_pt, st.Pe_filt, Pref, st.valve,
            st.x_kigov, st.x_kiload, st.x_tload, st.x_accel_lag, st.x_ka, g,
        )
        fsr = min(fsrn, fsra, fsrt)

        dx_kigov  = g.Kigov  * err_speed - g.Kbc_speed * (fsrn - fsr)
        dx_ka     = g.Ka     * err_accel - g.Kbc_accel * (fsra - fsr)
        dx_kiload = g.Kiload * err_temp  - g.Kbc_temp  * (fsrt - fsr)
        dx_accel_lag = (st.omega_pt - st.x_accel_lag) / g.Ta_s if g.Ta_s > 0 else 0.0

        # ---- Valve actuator ----
        valve_target = float(np.clip(fsr, g.Vmin_pu, g.Vmax_pu))
        raw_rate = (valve_target - st.valve) / max(g.Tact_s, 1e-6)
        rate = float(np.clip(raw_rate, g.Rclose_pu_s, g.Ropen_pu_s))
        if (st.valve >= g.Vmax_pu and rate > 0) or (st.valve <= g.Vmin_pu and rate < 0):
            rate = 0.0
        dvalve = rate

        # ---- Turbine block (turbine-base pu) ----
        Wf, dx_turb, Pm_gov = _turbine_power(st.valve, st.omega_pt, st.x_turb, g)

        # ---- Two-mass shaft ----
        # HP rotor (turbine base): governor turbine power minus gas-path
        # coupling delivered to PT.
        # NOTE: the HP rotor has no meaningful electrical angle — a former
        # delta_hp state integrated omega_hp-1 on the 377 rad/s electrical
        # base and drifted secularly at any part load. Removed (V&V Phase 1).
        P_couple = max(0.0, p.K_couple * (st.omega_hp - p.omega_hp_idle))
        domega_hp = (Pm_gov - P_couple - p.D_hp * (st.omega_hp - 1.0)) / p.M_hp

        # PT rotor (gen base): receives kb*P_couple, delivers Pe.
        domega_pt = (g.kb * P_couple - Pe_gen - p.D_pt * (st.omega_pt - 1.0)) / p.M_pt
        ddelta_pt = p.omega_0_rad_s * (st.omega_pt - 1.0)

        # ---- Temperature signal path (input = Wf, standard) ----
        if g.Tsb_s > 0:
            dx_tsab = (Wf - st.x_tsab) / g.Tsb_s
            tsab_out = st.x_tsab + g.Tsa_s * dx_tsab
        else:
            dx_tsab = 0.0
            tsab_out = Wf
        dx_tload = (tsab_out - st.x_tload) / g.Tfload_s if g.Tfload_s > 0 else 0.0

        # ---- Combustor lag for fuel reporting ----
        dP_fuel = (Wf - st.P_fuel) / g.T_comb_s

        return np.array([
            ddelta_pt, domega_pt, dPe_filt, dx_kigov, dx_ka, dx_accel_lag,
            dx_kiload, dx_tload, dx_tsab,
            dvalve, dx_turb, dP_fuel,
            domega_hp,
        ])

    return rhs


# ---------------------------------------------------------------------------
# IC
# ---------------------------------------------------------------------------

def _initial_state_for_load(Pe0_turb_pu: float, p: MultishaftParams) -> MultishaftState:
    """Self-consistent SS IC at Pe0 (pu on TURBINE base)."""
    g = p.ggov1
    valve_ss = Pe0_turb_pu / g.Kturb + g.Wfnl
    fsr_ss = valve_ss
    fsra_offset = g.Ka * g.aset_pu_s / g.Kbc_accel
    err_temp_ss = (g.Ldref / g.Kturb + g.Wfnl) - valve_ss
    fsrt_offset = g.Kiload * err_temp_ss / g.Kbc_temp
    x_ka_ss = fsr_ss + fsra_offset
    x_kiload_ss = (fsr_ss + fsrt_offset) - g.Kpload * err_temp_ss

    # HP rotor SS: P_couple_ss = Pe0_turb
    omega_hp_ss = p.omega_hp_idle + Pe0_turb_pu / p.K_couple

    return MultishaftState(
        delta_pt=0.0, omega_pt=1.0,
        Pe_filt=Pe0_turb_pu, x_kigov=fsr_ss, x_ka=x_ka_ss, x_accel_lag=1.0,
        x_kiload=x_kiload_ss, x_tload=valve_ss, x_tsab=valve_ss,
        valve=valve_ss, x_turb=valve_ss, P_fuel=valve_ss,
        omega_hp=omega_hp_ss,
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def simulate_multishaft(
    load_time_s: np.ndarray,
    load_demand_mw: np.ndarray,
    params: MultishaftParams = MultishaftParams(),
    sample_dt_s: float = 1.0,
    dispatch_fn: Optional[Callable[[np.ndarray], dict]] = None,
    initial_state: Optional[MultishaftState] = None,
) -> MultishaftResult:
    """Run Tier C two-mass mechanical + GGOV1 over a Load17-style profile."""
    load_time_s = np.asarray(load_time_s, dtype=float)
    load_demand_mw = np.asarray(load_demand_mw, dtype=float)
    if load_time_s.shape != load_demand_mw.shape:
        raise ValueError("shapes must match")
    if not np.all(np.diff(load_time_s) > 0):
        raise ValueError("load_time_s must be strictly increasing")
    if load_time_s.size < 2:
        raise ValueError("need at least two load samples")

    p = params
    g = p.ggov1
    Sn = g.Sn_mva
    load_gen_pu = load_demand_mw / Sn

    if load_time_s[0] > 0:
        load_time_s = np.concatenate([[0.0], load_time_s])
        load_gen_pu = np.concatenate([[load_gen_pu[0]], load_gen_pu])

    Pe0_gen = float(load_gen_pu[0])
    Pe0_turb = Pe0_gen / g.kb
    if Pe0_turb > g.Pm_thermal_max_pu * (1.0 + 1e-9):
        raise ValueError(
            f"Initial load {Pe0_gen * Sn:.2f} MW exceeds the temperature-"
            f"limited rating {g.Pm_thermal_max_pu * g.Trate_mw:.2f} MW."
        )
    Pref = _pref_for_rselect(Pe0_turb, g)
    state = initial_state if initial_state is not None else _initial_state_for_load(Pe0_turb, p)

    t_eval = np.arange(0.0, load_time_s[-1] + sample_dt_s, sample_dt_s)
    t_eval = t_eval[t_eval <= load_time_s[-1] + 1e-9]
    n_states = 13
    y_eval = np.empty((n_states, t_eval.size), dtype=float)
    y_eval[:, 0] = state.as_array()
    demand_gen_pu_eval = np.empty(t_eval.size, dtype=float)
    demand_gen_pu_eval[0] = Pe0_gen
    eval_idx = 1

    sol = None
    for k in range(load_time_s.size - 1):
        t0 = float(load_time_s[k])
        t1 = float(load_time_s[k + 1])
        Pe_chunk = float(load_gen_pu[k])
        rhs = _rhs_factory(p, Pref, Pe_chunk)
        sol = solve_ivp(
            rhs, (t0, t1), state.as_array(),
            method="RK45", max_step=p.max_step_s,
            rtol=p.rtol, atol=p.atol, dense_output=True,
        )
        if not sol.success:
            raise RuntimeError(f"solve_ivp failed in [{t0}, {t1}]: {sol.message}")
        while eval_idx < t_eval.size and t_eval[eval_idx] <= t1 + 1e-9:
            y_eval[:, eval_idx] = sol.sol(t_eval[eval_idx])
            demand_gen_pu_eval[eval_idx] = Pe_chunk
            eval_idx += 1
        state = MultishaftState.from_array(sol.y[:, -1])
    while eval_idx < t_eval.size:
        y_eval[:, eval_idx] = sol.sol(min(t_eval[eval_idx], load_time_s[-1]))
        demand_gen_pu_eval[eval_idx] = float(load_gen_pu[-2])
        eval_idx += 1

    # ---- Post-process ----
    delta_pt = y_eval[0]
    omega_pt = y_eval[1]
    valve = y_eval[9]
    x_turb = y_eval[10]
    P_fuel = y_eval[11]
    omega_hp = y_eval[12]

    freq_hz = omega_pt * 60.0
    NGG_full_rpm = 9500.0  # LM2500 NGG at full power (Pocket Guide)
    speed_hp_rpm = omega_hp * NGG_full_rpm

    V_ratio = p.V_term_pu / p.V_ref_pu
    Pe_gen = (demand_gen_pu_eval * (V_ratio ** p.pv_exponent)
              * (1.0 + g.alpha_load_damping * (omega_pt - 1.0)))
    Pe_mw = Pe_gen * Sn
    Pe_demand_mw_arr = demand_gen_pu_eval * Sn

    # Mechanical powers (turbine-base pu -> MW via Trate)
    Wf = valve * np.where(g.flag == 1, omega_pt, 1.0)
    if g.Dm < 0:
        Wf = Wf * omega_pt ** g.Dm
    if g.Tb_s > 0:
        dx_turb_arr = (Wf - x_turb) / g.Tb_s
        x_turb_out = x_turb + g.Tc_s * dx_turb_arr
    else:
        x_turb_out = Wf
    Pm_gov_pu = g.Kturb * (x_turb_out - g.Wfnl)
    if g.Dm > 0:
        Pm_gov_pu = Pm_gov_pu - g.Dm * (omega_pt - 1.0)
    Pm_hp_mw = Pm_gov_pu * g.Trate_mw

    P_couple_pu = np.maximum(0.0, p.K_couple * (omega_hp - p.omega_hp_idle))
    Pm_pt_mw = P_couple_pu * g.Trate_mw
    P_couple_mw = Pm_pt_mw.copy()

    result = MultishaftResult(
        t_s=t_eval,
        delta_pt_rad=delta_pt, omega_pt_pu=omega_pt, freq_hz=freq_hz,
        Pe_mw=Pe_mw, Pe_demand_mw=Pe_demand_mw_arr,
        omega_hp_pu=omega_hp, speed_hp_rpm=speed_hp_rpm,
        Pm_pt_mw=Pm_pt_mw, Pm_hp_mw=Pm_hp_mw, P_couple_mw=P_couple_mw,
        valve_pu=valve, P_fuel_pu=P_fuel,
    )

    # ---- Fuel (V&V fix G2, same semantics as ggov1) ----
    if dispatch_fn is not None:
        load_frac = np.clip(g.Kturb * (P_fuel - g.Wfnl), 0.0, 1.0)
        disp = dispatch_fn(load_frac)
        result.fuel_kg_s = np.asarray(disp["fuel_kg_s"], dtype=float)
    else:
        result.fuel_kg_s = g.wf_base_kg_s * np.clip(P_fuel, 0.0, None)
    result.cum_fuel_kg = np.concatenate(
        [[0.0],
         np.cumsum(0.5 * (result.fuel_kg_s[1:] + result.fuel_kg_s[:-1])
                   * np.diff(t_eval))]
    )

    return result

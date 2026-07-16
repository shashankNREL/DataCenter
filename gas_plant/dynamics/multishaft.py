"""Tier C (scipy): Two-mass mechanical shaft + voltage-sensitive load + GGOV1.

Adds to Tier B's GGOV1:

  - **Two-mass mechanical shaft model**: HP rotor (gas generator, ~10,000 rpm
    base) and PT/generator rotor (3600 rpm, direct-coupled). The HP rotor
    has its own swing equation driven by governor heat-rate output minus
    the coupling power that drives the PT rotor. Makes the HP-rotor speed
    transient (~0.5-1 s time constant) visible — the dynamic that drives
    real part-load fuel response and surge margin.

  - **Voltage-sensitive load via ZIP exponent**: Pe(t,ω,V) = demand(t) *
    (V/Vref)^pv * (1 + α(ω-1)). No internal V dynamics here — V is held
    at 1 pu (perfect AVR assumption). For studies needing real V-Q
    dynamics, use the ANDES path (gas_plant_andes) which has GENROU + EXST1.

The 2-mass coupling: governor's Pmech is delivered to the HP rotor; the
HP rotor drives the PT rotor via a simple speed-difference coupling
P_couple = K_couple * (omega_hp - omega_hp_idle). At steady state, the
coupling balances exactly, so HP and PT rotate at their respective speeds
in lock. During transients, the HP rotor's lighter inertia accelerates
faster — visible as a short-lived HP-speed overshoot, which then settles
when the coupling rebalances.

State vector (14 states = 12 GGOV1 + 2 HP-rotor additions):
    delta_pt, omega_pt   [PT/gen rotor swing eq]
    Pe_filt
    x_kigov, x_ka, x_kiload
    x_accel_lag
    x_tload, x_tsab
    valve, x_turb
    P_fuel
    delta_hp, omega_hp   [Tier C addition: HP rotor swing eq]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import pandas as pd
from scipy.integrate import solve_ivp

from .ggov1 import GGOV1Params, _compute_controllers


# ---------------------------------------------------------------------------
# Parameters and state
# ---------------------------------------------------------------------------

@dataclass
class MultishaftParams:
    """Tier C parameters: GGOV1 + two-mass shaft + ZIP-exponent load."""

    ggov1: GGOV1Params = field(default_factory=GGOV1Params.lm2500_overrides)

    # ----- Two-mass split -----
    # Total LM2500 system H is split between HP rotor (light, fast) and
    # PT/generator rotor (heavy, slow). Typical aero gen-set: HP ~ 20-30 %
    # of total kinetic energy (small mass at high speed), PT+gen ~ 70-80 %.
    H_pt_s: float = 2.5      # PT + generator rotor inertia (s) — was 2.8 (TGOV1 lumped)
    H_hp_s: float = 0.3      # HP rotor inertia (s) — small fast rotor
    D_pt: float = 1.0        # PT/gen rotor damping (pu/pu) — referenced to 60 Hz
    # D_hp default 0: HP rotor's "natural" speed is the gas-path operating
    # point, NOT 60 Hz. Standard `D*(omega - 1)` damping would incorrectly
    # pull HP toward 60 Hz. The gas-path coupling P_couple(omega_hp) already
    # provides the effective damping. Set nonzero only if modeling bearing
    # loss explicitly (and reference to the operating-point speed, not 1.0).
    D_hp: float = 0.0

    # Coupling: P_couple_to_pt = K_couple * (omega_hp - omega_hp_idle)
    # At omega_hp = 1.0 (full NGG ~9500 rpm), full power is delivered to PT.
    # At omega_hp = omega_hp_idle (~4900/9500 = 0.516 NGG_idle), zero power.
    omega_hp_idle: float = 0.516
    # K_couple set so that omega_hp=1.0 gives 1.0 pu power to PT
    K_couple: float = field(init=False)

    # ----- ZIP voltage exponent on Pe -----
    # Pe = demand * (V/Vref)^pv. pv=0 -> constant-P. pv=2 -> constant-Z.
    # Real loads are a mix; data centers are ~0.5-1.5 (mostly P, some Z).
    pv_exponent: float = 0.0    # default 0 (constant-P) preserves Tier B behavior
    V_term_pu: float = 1.0      # held constant in scipy Tier C (use ANDES path for V dynamics)
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
        # K_couple makes P_couple(omega_hp=1) = 1.0 pu
        self.K_couple = 1.0 / max(1.0 - self.omega_hp_idle, 1e-6)


@dataclass
class MultishaftState:
    delta_pt: float = 0.0
    omega_pt: float = 1.0
    Pe_filt: float = 0.0
    x_kigov: float = 0.0
    x_ka: float = 0.0
    x_accel_lag: float = 1.0
    x_kiload: float = 0.0
    x_tload: float = 0.0
    x_tsab: float = 0.0
    valve: float = 0.0
    x_turb: float = 0.0
    P_fuel: float = 0.0
    delta_hp: float = 0.0
    omega_hp: float = 1.0

    def as_array(self) -> np.ndarray:
        return np.array([
            self.delta_pt, self.omega_pt, self.Pe_filt, self.x_kigov,
            self.x_ka, self.x_accel_lag, self.x_kiload, self.x_tload,
            self.x_tsab, self.valve, self.x_turb, self.P_fuel,
            self.delta_hp, self.omega_hp,
        ], dtype=float)

    @classmethod
    def from_array(cls, y: np.ndarray) -> "MultishaftState":
        return cls(
            delta_pt=float(y[0]), omega_pt=float(y[1]), Pe_filt=float(y[2]),
            x_kigov=float(y[3]), x_ka=float(y[4]), x_accel_lag=float(y[5]),
            x_kiload=float(y[6]), x_tload=float(y[7]), x_tsab=float(y[8]),
            valve=float(y[9]), x_turb=float(y[10]), P_fuel=float(y[11]),
            delta_hp=float(y[12]), omega_hp=float(y[13]),
        )


@dataclass
class MultishaftResult:
    t_s: np.ndarray
    # Electrical / PT rotor
    delta_pt_rad: np.ndarray
    omega_pt_pu: np.ndarray
    freq_hz: np.ndarray
    Pe_mw: np.ndarray
    Pe_demand_mw: np.ndarray
    # HP rotor — Tier C novelty
    omega_hp_pu: np.ndarray
    speed_hp_rpm: np.ndarray
    delta_hp_rad: np.ndarray
    # Mechanical
    Pm_pt_mw: np.ndarray       # power delivered to PT from coupling
    Pm_hp_mw: np.ndarray       # governor's heat-rate equivalent power to HP rotor
    P_couple_mw: np.ndarray    # power transmitted through HP->PT coupling
    valve_pu: np.ndarray
    P_fuel_pu: np.ndarray
    # Fuel
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
            "delta_hp_rad": self.delta_hp_rad,
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

def _rhs_factory(params: MultishaftParams, Pref: float, P_demand_pu: float):
    """Build chunked RHS for a constant Pe_demand segment."""
    p = params
    g = p.ggov1

    def rhs(t: float, y: np.ndarray) -> np.ndarray:
        st = MultishaftState.from_array(y)

        # ---- Pe with frequency damping + voltage exponent ----
        # V is held at V_term_pu (no V dynamics in scipy Tier C);
        # the (V/Vref)^pv factor is just a scalar.
        V_ratio = p.V_term_pu / p.V_ref_pu
        Pe = P_demand_pu * (V_ratio ** p.pv_exponent) * (1.0 + g.alpha_load_damping * (st.omega_pt - 1.0))

        # ---- Power transducer ----
        if g.Tpelec_s > 0:
            dPe_filt = (Pe - st.Pe_filt) / g.Tpelec_s
        else:
            dPe_filt = 0.0

        # ---- GGOV1 controllers ----
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

        # ---- Turbine block: Wf -> lead-lag -> Pmech_gov ----
        # NOTE: governor speed feedback uses PT-rotor speed (omega_pt),
        # because that's the speed measured by the speed pickups on the
        # power-turbine shaft (the LM2500 governor regulates PT speed = 3600 rpm).
        Wf = st.valve * (st.omega_pt if g.flag == 1 else 1.0)
        if g.Tb_s > 0:
            dx_turb = (Wf - st.x_turb) / g.Tb_s
        else:
            dx_turb = 0.0
        x_turb_out = st.x_turb + g.Tc_s * dx_turb
        Pm_gov = g.Kturb * (x_turb_out - g.Wfnl) + g.Dm * (st.omega_pt - 1.0)

        # ---- Two-mass shaft ----
        # HP rotor: governor's heat-rate-equivalent power minus coupling to PT.
        # The "coupling" is the power transmitted through the gas path
        # (HPT exhaust -> IPT -> FPT). At SS this equals Pm_gov (so HP
        # rotor RHS is zero); during transients, the HP rotor accelerates
        # when Pm_gov exceeds P_couple.
        P_couple = max(0.0, p.K_couple * (st.omega_hp - p.omega_hp_idle))
        domega_hp = (Pm_gov - P_couple - p.D_hp * (st.omega_hp - 1.0)) / p.M_hp
        ddelta_hp = p.omega_0_rad_s * (st.omega_hp - 1.0)

        # PT rotor: receives P_couple from the gas path, delivers Pe to load.
        domega_pt = (P_couple - Pe - p.D_pt * (st.omega_pt - 1.0)) / p.M_pt
        ddelta_pt = p.omega_0_rad_s * (st.omega_pt - 1.0)

        # ---- Temperature signal path (from Tier B) ----
        tex = Pm_gov / g.Kturb + g.Wfnl
        if g.Tsb_s > 0:
            dx_tsab = (tex - st.x_tsab) / g.Tsb_s
        else:
            dx_tsab = 0.0
        tsab_out = st.x_tsab + g.Tsa_s * dx_tsab
        if g.Tfload_s > 0:
            dx_tload = (tsab_out - st.x_tload) / g.Tfload_s
        else:
            dx_tload = 0.0

        # ---- Combustor lag for fuel reporting ----
        dP_fuel = (st.valve - st.P_fuel) / g.T_comb_s

        return np.array([
            ddelta_pt, domega_pt, dPe_filt, dx_kigov, dx_ka, dx_accel_lag,
            dx_kiload, dx_tload, dx_tsab,
            dvalve, dx_turb, dP_fuel,
            ddelta_hp, domega_hp,
        ])

    return rhs


# ---------------------------------------------------------------------------
# IC
# ---------------------------------------------------------------------------

def _initial_state_for_load(Pe0_pu: float, p: MultishaftParams) -> MultishaftState:
    """Self-consistent SS IC at Pe0_pu."""
    g = p.ggov1
    valve_ss = (Pe0_pu / g.Kturb + g.Wfnl) / 1.0
    fsr_ss = valve_ss
    fsra_offset = g.Ka * g.aset_pu_s / g.Kbc_accel
    err_temp_ss = (g.Ldref / g.Kturb + g.Wfnl) - valve_ss
    fsrt_offset = g.Kiload * err_temp_ss / g.Kbc_temp
    x_ka_ss = fsr_ss + fsra_offset
    x_kiload_ss = (fsr_ss + fsrt_offset) - g.Kpload * err_temp_ss

    # HP rotor SS: P_couple_ss = Pe0, so omega_hp_ss = omega_hp_idle + Pe0/K_couple
    omega_hp_ss = p.omega_hp_idle + Pe0_pu / p.K_couple

    return MultishaftState(
        delta_pt=0.0, omega_pt=1.0,
        Pe_filt=Pe0_pu, x_kigov=fsr_ss, x_ka=x_ka_ss, x_accel_lag=1.0,
        x_kiload=x_kiload_ss, x_tload=valve_ss, x_tsab=valve_ss,
        valve=valve_ss, x_turb=valve_ss, P_fuel=valve_ss,
        delta_hp=0.0, omega_hp=omega_hp_ss,
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

    g = params.ggov1
    Sn = g.Sn_mva
    load_pu = load_demand_mw / Sn

    if load_time_s[0] > 0:
        load_time_s = np.concatenate([[0.0], load_time_s])
        load_pu = np.concatenate([[load_pu[0]], load_pu])

    Pe0 = float(load_pu[0])
    Pref = Pe0
    state = initial_state if initial_state is not None else _initial_state_for_load(Pe0, params)

    t_eval = np.arange(0.0, load_time_s[-1] + sample_dt_s, sample_dt_s)
    n_states = 14
    y_eval = np.empty((n_states, t_eval.size), dtype=float)
    y_eval[:, 0] = state.as_array()
    eval_idx = 1

    for k in range(load_time_s.size - 1):
        t0 = float(load_time_s[k])
        t1 = float(load_time_s[k + 1])
        Pe_chunk = float(load_pu[k])
        rhs = _rhs_factory(params, Pref, Pe_chunk)
        sol = solve_ivp(
            rhs, (t0, t1), state.as_array(),
            method="RK45", max_step=params.max_step_s,
            rtol=params.rtol, atol=params.atol, dense_output=True,
        )
        if not sol.success:
            raise RuntimeError(f"solve_ivp failed in [{t0}, {t1}]: {sol.message}")
        while eval_idx < t_eval.size and t_eval[eval_idx] <= t1 + 1e-9:
            y_eval[:, eval_idx] = sol.sol(t_eval[eval_idx])
            eval_idx += 1
        state = MultishaftState.from_array(sol.y[:, -1])
    while eval_idx < t_eval.size:
        y_eval[:, eval_idx] = sol.sol(t_eval[eval_idx])
        eval_idx += 1

    # ---- Post-process ----
    delta_pt = y_eval[0]
    omega_pt = y_eval[1]
    valve = y_eval[9]
    x_turb = y_eval[10]
    P_fuel = y_eval[11]
    delta_hp = y_eval[12]
    omega_hp = y_eval[13]

    freq_hz = omega_pt * 60.0
    NGG_full_rpm = 9500.0  # LM2500 NGG at full power
    speed_hp_rpm = omega_hp * NGG_full_rpm

    # Pe(t) on the eval grid
    Pe_mw = np.empty_like(t_eval)
    Pe_demand_mw_arr = np.empty_like(t_eval)
    k = 0
    for i, t in enumerate(t_eval):
        while k + 1 < load_time_s.size and load_time_s[k + 1] <= t:
            k += 1
        Pdem_pu = load_pu[k]
        V_ratio = params.V_term_pu / params.V_ref_pu
        Pe_pu = Pdem_pu * (V_ratio ** params.pv_exponent) * (1.0 + g.alpha_load_damping * (omega_pt[i] - 1.0))
        Pe_mw[i] = Pe_pu * Sn
        Pe_demand_mw_arr[i] = Pdem_pu * Sn

    # Mechanical powers
    Wf = valve * np.where(g.flag == 1, omega_pt, 1.0)
    dx_turb_arr = (Wf - x_turb) / g.Tb_s if g.Tb_s > 0 else np.zeros_like(x_turb)
    x_turb_out = x_turb + g.Tc_s * dx_turb_arr
    Pm_gov_pu = g.Kturb * (x_turb_out - g.Wfnl) + g.Dm * (omega_pt - 1.0)
    Pm_hp_mw = Pm_gov_pu * Sn

    P_couple_pu = np.maximum(0.0, params.K_couple * (omega_hp - params.omega_hp_idle))
    Pm_pt_mw = P_couple_pu * Sn
    P_couple_mw = Pm_pt_mw.copy()

    result = MultishaftResult(
        t_s=t_eval,
        delta_pt_rad=delta_pt, omega_pt_pu=omega_pt, freq_hz=freq_hz,
        Pe_mw=Pe_mw, Pe_demand_mw=Pe_demand_mw_arr,
        omega_hp_pu=omega_hp, speed_hp_rpm=speed_hp_rpm, delta_hp_rad=delta_hp,
        Pm_pt_mw=Pm_pt_mw, Pm_hp_mw=Pm_hp_mw, P_couple_mw=P_couple_mw,
        valve_pu=valve, P_fuel_pu=P_fuel,
    )

    if dispatch_fn is not None:
        load_frac = np.clip(P_fuel * Sn / g.P_turbine_mw, 0.0, 1.0)
        disp = dispatch_fn(load_frac)
        result.fuel_kg_s = np.asarray(disp["fuel_kg_s"], dtype=float)
        result.cum_fuel_kg = np.concatenate(
            [[0.0],
             np.cumsum(0.5 * (result.fuel_kg_s[1:] + result.fuel_kg_s[:-1])
                       * np.diff(t_eval))]
        )

    return result

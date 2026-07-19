"""Tier B: GGOV1 governor + swing equation for the LM2500.

Implements the IEEE PES-TR1 (2013) Fig 3-5 GGOV1 gas-turbine governor model
in pure scipy, with the three principal control loops:

  - Speed/power governor (PI on speed error + selectable droop feedback)
  - Acceleration controller (integral on filtered angular acceleration)
  - Temperature/load limiter (PI on a fuel-flow-derived temperature proxy)

arbitrated through a low-value-select (LVG) on `fsrn`, `fsra`, `fsrt`.
Back-calculation anti-windup keeps the unselected controllers' integrators
tracking the valve command so they re-engage cleanly.

PER-UNIT SYSTEM (V&V Phase 0, fix G1 — see docs/vv_log.md):
  All governor-internal signals (valve stroke, Wf, fsr*, Pm_turb, Ldref,
  Vmax/Vmin, rate limits) are per-unit on the TURBINE MW base `Trate_mw`,
  exactly as PES-TR1 §3.3 prescribes (PSS/E's `Trate`). The swing equation
  runs on the GENERATOR MVA base `Sn_mva`. The single conversion factor

      kb = Trate_mw / Sn_mva      (pu_gen = pu_turbine * kb)

  is applied only at the two interfaces: Pm into the swing equation and Pe
  into the power transducer. Consequences (LM2500: Trate=22, Sn=23):
    - Vmax = 1.0 is a valve STROKE limit (not a power limit).
    - The thermal cap is enforced by the temperature limiter at
      Pm = Ldref (pu turbine) = Ldref * Trate_mw (MW) = 22 MW for Ldref=1.
    - Transient ceiling at valve limit: Kturb*(Vmax - Wfnl) = 1.2 pu
      turbine = 26.4 MW (short-term capability until the temp limiter,
      with its Tfload/Kpload/Kiload dynamics, winds the fuel back).

DROOP FEEDBACK (fix G4 — rselect now honored):
    rselect =  1 : electrical power (through Tpelec transducer)  [default]
    rselect =  0 : isochronous (no droop feedback; integrator drives
                   frequency exactly back to nominal — the natural mode for
                   a single islanded unit)
    rselect = -1 : valve stroke feedback
    rselect = -2 : governor output feedback (algebraic loop solved in
                   closed form)

Dm SEMANTICS (fix G3, per PES-TR1 / PSS/E GGOV1 spec):
    Dm > 0 : Pm_turb -= Dm * (omega - 1)     (speed damping)
    Dm < 0 : Wf *= omega**Dm                 (fuel-flow speed sensitivity)

FUEL ACCOUNTING (fix G2): fuel mass flow is computed natively from the
GGOV1 fuel signal:  fuel_kg_s = wf_base_kg_s * Wf_pu, where wf_base_kg_s is
calibrated so that at rated output (Pm = Ldref) the unit burns
Trate_mw*1e6/(eta_design*fuel_lhv_j_kg).  The optional `dispatch_fn` path
converts the fuel signal to an equivalent POWER load fraction
(Kturb*(Wf - Wfnl)) before calling the surrogate — the pre-fix code passed
the raw valve stroke as a load fraction, double-counting no-load fuel.

State vector (12 states):
    delta        rotor angle (rad)
    omega        rotor speed, per-unit on omega_0 = 2*pi*60
    Pe_filt      Pe through Tpelec lag (pu, TURBINE base)
    x_kigov      speed PI integrator state
    x_ka         acceleration-controller integrator (= fsra signal)
    x_accel_lag  speed lag for d/dt filter (Ta block)
    x_kiload     temperature PI integrator state
    x_tload      temperature signal lag (Tfload block)
    x_tsab       temperature signal lead-lag (Tsa/Tsb block), input = Wf
    valve        fuel-valve stroke (pu turbine base), after Tact + rate-limit
    x_turb       turbine lead-lag state (Tb/Tc on fuel)
    P_fuel       combustor lag on the fuel signal Wf (for fuel reporting)

Implemented subset: Kdgov/Tdgov (derivative), deadband db, KIMW/Pmwset
outer-MW loop, Rup/Rdown, and Teng are NOT implemented (all default to
inactive in PES-TR1 Appendix C). Documented in docs/vv_log.md.
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

    Governor per-unit base = `Trate_mw` (turbine MW), per PES-TR1 §3.3.
    Swing-equation per-unit base = `Sn_mva` (generator MVA).
    """

    # ----- Machine / swing equation (per Sn_mva) -----
    Sn_mva: float = 23.0
    H_s: float = 2.8
    # D=0: load-frequency sensitivity is modeled explicitly via
    # alpha_load_damping (Kundur 1994 §11.1.4); a nonzero machine D on top
    # of that double-counts damping (V&V fix G5).
    D_pu: float = 0.0

    # ----- Turbine rating (governor per-unit base) -----
    Trate_mw: float = 22.0   # LM2500 base rating; PES-TR1 'MWCAP'/'Trate'

    # ----- GGOV1 speed/power governor -----
    R: float = 0.04              # droop (pu on Trate)
    rselect: int = 1             # 1=elec power, 0=isochronous, -1=valve, -2=gov out
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
    Ldref: float = 1.0           # load (thermal) limit, pu on Trate_mw
    Kpload: float = 2.0          # temp PI proportional
    Kiload: float = 0.67         # temp PI integral
    Tfload_s: float = 3.0        # temperature filter lag
    Tsa_s: float = 4.0           # temperature signal lead
    Tsb_s: float = 5.0           # temperature signal lag

    # ----- Valve actuator (all pu on Trate) -----
    Tact_s: float = 0.5          # actuator time constant (PES-TR1 default)
    Vmax_pu: float = 1.0         # valve STROKE limit (PES-TR1 default)
    Vmin_pu: float = 0.15        # PES-TR1 default (below Wfnl: negative Pm
                                 # is physical — compressor drag exceeds
                                 # turbine output below no-load fuel)
    Ropen_pu_s: float = 0.10     # valve open rate limit
    Rclose_pu_s: float = -0.10   # valve close rate limit

    # ----- Turbine block -----
    Kturb: float = 1.5           # turbine gain: Pm = Kturb*(Wf - Wfnl)
    Wfnl: float = 0.2            # full-speed-no-load fuel (pu on Trate)
    Tb_s: float = 0.1            # turbine lag
    Tc_s: float = 0.0            # turbine lead (0 disables lead-lag)
    Teng_s: float = 0.0          # engine transport delay (not implemented; 0 for GT)
    flag: int = 1                # 1 = fuel flow proportional to speed

    # ----- Speed sensitivity of turbine output -----
    Dm: float = 0.0              # >0: Pm -= Dm*dw ; <0: Wf *= omega**Dm

    # ----- Load model / fuel reporting -----
    alpha_load_damping: float = 1.5   # Kundur §11.1.4 typical 1-2
    T_comb_s: float = 0.3             # combustor lag on fuel signal

    # ----- Native fuel calibration (V&V fix G2) -----
    eta_design: float = 0.365         # gen-set efficiency at rated (see constants.md)
    fuel_lhv_j_kg: float = 49e6       # natural-gas LHV

    # ----- Anti-windup tracking gain (back-calculation) -----
    # Always-active back-calculation:
    #   dx_kigov/dt = Kigov*err_speed - Kbc_speed*(fsrn - fsr)
    # When selected, (fsrn - fsr)=0 (pure PI); when not selected the term
    # pulls the integrator to track fsr. Smooth — no if/else on selection.
    # Sensitivity of results to Kbc across a 25x range verified: 2.1 mHz
    # spread on a step-response nadir (tests/test_dynamics.py::
    # test_kbc_sensitivity, gated at 5 mHz).
    Kbc_speed: float = 100.0
    Kbc_accel: float = 100.0
    Kbc_temp: float = 50.0

    # ----- Integrator -----
    rtol: float = 1e-6
    atol: float = 1e-8
    max_step_s: float = 0.5

    # ----- Derived (cached) -----
    omega_0_rad_s: float = field(default=2.0 * np.pi * 60.0, init=False)
    M_pu_s: float = field(init=False)
    kb: float = field(init=False)             # Trate/Sn: pu_turb -> pu_gen
    wf_base_kg_s: float = field(init=False)   # kg/s per pu of Wf

    def __post_init__(self) -> None:
        self.M_pu_s = 2.0 * self.H_s
        self.kb = self.Trate_mw / self.Sn_mva
        # Fuel calibration: at rated output Pm_turb = Ldref, the fuel signal
        # is Wf_rated = Ldref/Kturb + Wfnl and the physical burn is
        # P_rated / (eta_design * LHV).
        wf_rated = self.Ldref / self.Kturb + self.Wfnl
        fuel_rated_kg_s = (self.Trate_mw * 1e6) / (self.eta_design * self.fuel_lhv_j_kg)
        self.wf_base_kg_s = fuel_rated_kg_s / wf_rated

    # Steady-state ceilings (turbine-base pu)
    @property
    def Pm_transient_max_pu(self) -> float:
        """Short-term ceiling at the valve stroke limit (before temp limiter)."""
        return self.Kturb * (self.Vmax_pu - self.Wfnl)

    @property
    def Pm_thermal_max_pu(self) -> float:
        """Continuous (temperature-limited) ceiling."""
        return self.Ldref

    @classmethod
    def lm2500_overrides(cls, **extra) -> "GGOV1Params":
        """LM2500-specific overrides on top of PES-TR1 Appendix C defaults.

        Justifications in docs/constants.md and docs/tier_plan.md Tier B.
        """
        defaults = dict(
            Tact_s=0.15,   # Woodward MkVIe/NetCon electronic actuator, faster
                           # than the heavy-duty hydraulic PES-TR1 default
            R=0.04,        # 4% droop, aero islanded (Pocket Guide §1.3.17)
            # All other parameters: PES-TR1 Appendix C typical values.
        )
        defaults.update(extra)
        return cls(**defaults)


@dataclass
class GGOV1State:
    """Initial / instantaneous ODE state for the 12-state GGOV1 model."""

    delta: float = 0.0
    omega: float = 1.0
    Pe_filt: float = 0.0      # turbine-base pu
    x_kigov: float = 0.0
    x_ka: float = 0.0
    x_accel_lag: float = 1.0  # init to omega so derivative starts at 0
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
    """Time-resolved GGOV1 simulation.

    `Pe_mw` is the ACTUAL electrical power including the frequency-damping
    term (V&V fix G4 diagnostics); `Pe_demand_mw` is the raw ZOH demand.
    `valve_pu`, `P_fuel_pu`, and the fsr* diagnostics are pu on Trate_mw.
    """

    t_s: np.ndarray
    delta_rad: np.ndarray
    omega_pu: np.ndarray
    freq_hz: np.ndarray
    speed_rpm: np.ndarray
    Pm_mw: np.ndarray
    Pm_pu: np.ndarray          # gen base (Sn_mva), for swing-eq consistency
    Pe_mw: np.ndarray          # actual, incl. load damping
    Pe_pu: np.ndarray          # gen base
    Pe_demand_mw: np.ndarray   # raw ZOH demand
    valve_pu: np.ndarray
    P_fuel_pu: np.ndarray
    fsrn_pu: np.ndarray
    fsra_pu: np.ndarray
    fsrt_pu: np.ndarray
    fsr_pu: np.ndarray
    fuel_kg_s: Optional[np.ndarray] = None
    cum_fuel_kg: Optional[np.ndarray] = None

    def as_dataframe(self) -> pd.DataFrame:
        cols = {
            "t_s": self.t_s, "delta_rad": self.delta_rad,
            "omega_pu": self.omega_pu, "freq_hz": self.freq_hz,
            "speed_rpm": self.speed_rpm,
            "Pm_mw": self.Pm_mw, "Pm_pu": self.Pm_pu,
            "Pe_mw": self.Pe_mw, "Pe_pu": self.Pe_pu,
            "Pe_demand_mw": self.Pe_demand_mw,
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
# Controller computation
# ---------------------------------------------------------------------------

def _compute_controllers(omega, Pe_filt, Pref, valve, x_kigov, x_kiload,
                         x_tload, x_accel_lag, x_ka, p):
    """Compute the three would-be controller outputs (fsrn, fsra, fsrt).

    All power-like quantities are pu on the TURBINE base. `Pref` is the
    power (or valve/governor-output) reference matching `p.rselect`; it is
    ignored for rselect=0 (isochronous).
    """
    # ----- Speed governor: droop feedback per rselect -----
    if p.rselect == 1:
        fb = Pe_filt
        err_speed = (1.0 - omega) + p.R * (Pref - fb)
    elif p.rselect == 0:
        err_speed = (1.0 - omega)
    elif p.rselect == -1:
        fb = valve
        err_speed = (1.0 - omega) + p.R * (Pref - fb)
    elif p.rselect == -2:
        # Governor-output feedback: fsrn = Kpgov*err + x_kigov and
        # err = (1-w) + R*(Pref - fsrn)  =>  closed-form solve.
        err_speed = ((1.0 - omega) + p.R * (Pref - x_kigov)) / (1.0 + p.R * p.Kpgov)
    else:
        raise ValueError(f"rselect must be one of 1, 0, -1, -2; got {p.rselect}")

    err_speed = float(np.clip(err_speed, p.MinERR, p.MaxERR))
    fsrn = p.Kpgov * err_speed + x_kigov

    # ----- Acceleration controller (integral on aset error) -----
    accel = (omega - x_accel_lag) / p.Ta_s if p.Ta_s > 0 else 0.0
    err_accel = p.aset_pu_s - accel
    fsra = x_ka  # pure integrator output

    # ----- Temperature limiter (PI on fuel-derived temperature error) -----
    tlim = p.Ldref / p.Kturb + p.Wfnl     # fuel signal at the thermal limit
    err_temp = tlim - x_tload             # positive when below limit
    fsrt = p.Kpload * err_temp + x_kiload

    return fsrn, fsra, fsrt, err_speed, err_accel, err_temp


def _turbine_power(valve, omega, x_turb, p):
    """Wf, dx_turb, Pm_turb (pu on Trate). Implements flag and Dm semantics."""
    Wf = valve * (omega if p.flag == 1 else 1.0)
    if p.Dm < 0:
        Wf = Wf * omega ** p.Dm
    if p.Tb_s > 0:
        dx_turb = (Wf - x_turb) / p.Tb_s
        x_turb_out = x_turb + p.Tc_s * dx_turb
    else:
        dx_turb = 0.0
        x_turb_out = Wf
    Pm_turb = p.Kturb * (x_turb_out - p.Wfnl)
    if p.Dm > 0:
        Pm_turb = Pm_turb - p.Dm * (omega - 1.0)
    return Wf, dx_turb, Pm_turb


def _rhs_factory(params: GGOV1Params, Pref: float, Pe_load_gen_pu: float):
    """Build a chunked-RHS closure for a constant Pe_load segment.

    `Pe_load_gen_pu` is the demand on the GENERATOR base.
    """
    p = params

    def rhs(t: float, y: np.ndarray) -> np.ndarray:
        (delta, omega, Pe_filt, x_kigov, x_ka, x_accel_lag, x_kiload,
         x_tload, x_tsab, valve, x_turb, P_fuel) = y

        # --- Electrical power with load damping (gen base) ---
        Pe_gen = Pe_load_gen_pu * (1.0 + p.alpha_load_damping * (omega - 1.0))
        Pe_turb = Pe_gen / p.kb

        # --- Power transducer (turbine base) ---
        if p.Tpelec_s > 0:
            dPe_filt = (Pe_turb - Pe_filt) / p.Tpelec_s
        else:
            Pe_filt = Pe_turb
            dPe_filt = 0.0

        # --- Controllers ---
        fsrn, fsra, fsrt, err_speed, err_accel, err_temp = _compute_controllers(
            omega, Pe_filt, Pref, valve, x_kigov, x_kiload, x_tload,
            x_accel_lag, x_ka, p,
        )

        # --- LVG: lowest signal wins (PES-TR1 §3.1.2.3) ---
        fsr = min(fsrn, fsra, fsrt)

        # --- Always-active back-calculation anti-windup ---
        dx_kigov  = p.Kigov  * err_speed - p.Kbc_speed * (fsrn - fsr)
        dx_ka     = p.Ka     * err_accel - p.Kbc_accel * (fsra - fsr)
        dx_kiload = p.Kiload * err_temp  - p.Kbc_temp  * (fsrt - fsr)

        # Acceleration speed-lag (for derivative filter)
        if p.Ta_s > 0:
            dx_accel_lag = (omega - x_accel_lag) / p.Ta_s
        else:
            dx_accel_lag = 0.0

        # --- Valve actuator (Tact + rate limit + Vmin/Vmax non-windup clamp) ---
        valve_target = float(np.clip(fsr, p.Vmin_pu, p.Vmax_pu))
        raw_rate = (valve_target - valve) / max(p.Tact_s, 1e-6)
        rate = float(np.clip(raw_rate, p.Rclose_pu_s, p.Ropen_pu_s))
        if (valve >= p.Vmax_pu and rate > 0) or (valve <= p.Vmin_pu and rate < 0):
            rate = 0.0
        dvalve = rate

        # --- Turbine block ---
        Wf, dx_turb, Pm_turb = _turbine_power(valve, omega, x_turb, p)

        # --- Swing equation (gen base): Pm_gen = kb * Pm_turb ---
        domega = (p.kb * Pm_turb - Pe_gen - p.D_pu * (omega - 1.0)) / p.M_pu_s
        ddelta = p.omega_0_rad_s * (omega - 1.0)

        # --- Temperature signal path: input is the FUEL FLOW Wf (standard),
        #     lead-lag (1+sTsa)/(1+sTsb), then lag 1/(1+sTfload) ---
        if p.Tsb_s > 0:
            dx_tsab = (Wf - x_tsab) / p.Tsb_s
            tsab_out = x_tsab + p.Tsa_s * dx_tsab
        else:
            dx_tsab = 0.0
            tsab_out = Wf
        if p.Tfload_s > 0:
            dx_tload = (tsab_out - x_tload) / p.Tfload_s
        else:
            dx_tload = 0.0

        # --- Combustor lag on the fuel signal (for fuel reporting) ---
        dP_fuel = (Wf - P_fuel) / p.T_comb_s

        return np.array([ddelta, domega, dPe_filt, dx_kigov, dx_ka,
                         dx_accel_lag, dx_kiload, dx_tload, dx_tsab,
                         dvalve, dx_turb, dP_fuel])

    return rhs


# ---------------------------------------------------------------------------
# Initial-condition solver
# ---------------------------------------------------------------------------

def _initial_state_for_load(Pe0_turb_pu: float, p: GGOV1Params) -> GGOV1State:
    """Self-consistent steady state at Pe0 (pu on TURBINE base), omega = 1.

    At SS with the back-calculation form:
      valve_ss = Pe0/Kturb + Wfnl ; fsr = valve
      speed loop:  err_speed = 0, x_kigov = fsr
      accel loop:  fsra - fsr = Ka*aset/Kbc_accel
      temp loop:   fsrt - fsr = Kiload*err_temp/Kbc_temp
    """
    valve_ss = Pe0_turb_pu / p.Kturb + p.Wfnl
    fsr_ss = valve_ss

    fsra_offset = p.Ka * p.aset_pu_s / p.Kbc_accel
    err_temp_ss = (p.Ldref / p.Kturb + p.Wfnl) - valve_ss
    fsrt_offset = p.Kiload * err_temp_ss / p.Kbc_temp

    x_ka_ss = fsr_ss + fsra_offset
    x_kiload_ss = (fsr_ss + fsrt_offset) - p.Kpload * err_temp_ss

    return GGOV1State(
        delta=0.0,
        omega=1.0,
        Pe_filt=Pe0_turb_pu,
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


def _pref_for_rselect(Pe0_turb_pu: float, p: GGOV1Params) -> float:
    """Reference matching the droop-feedback signal at the initial SS."""
    if p.rselect == 1:
        return Pe0_turb_pu
    if p.rselect == 0:
        return 0.0  # unused
    # rselect -1 / -2: feedback is valve stroke / governor output = valve_ss
    return Pe0_turb_pu / p.Kturb + p.Wfnl


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
    """Run GGOV1 over a Load17-style step profile (ZOH demand in MW)."""
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
    load_gen_pu = load_demand_mw / Sn

    if load_time_s[0] > 0:
        load_time_s = np.concatenate([[0.0], load_time_s])
        load_gen_pu = np.concatenate([[load_gen_pu[0]], load_gen_pu])

    Pe0_gen = float(load_gen_pu[0])
    Pe0_turb = Pe0_gen / p.kb
    # Continuous-capability guard (V&V fix G1): the temperature limiter caps
    # steady Pm at Ldref; refuse to initialize above it.
    if Pe0_turb > p.Pm_thermal_max_pu * (1.0 + 1e-9):
        raise ValueError(
            f"Initial load {Pe0_gen * Sn:.2f} MW exceeds the temperature-"
            f"limited rating {p.Pm_thermal_max_pu * p.Trate_mw:.2f} MW."
        )
    Pref = _pref_for_rselect(Pe0_turb, p)
    state = initial_state if initial_state is not None else _initial_state_for_load(Pe0_turb, p)

    t_eval = np.arange(0.0, load_time_s[-1] + sample_dt_s, sample_dt_s)
    t_eval = t_eval[t_eval <= load_time_s[-1] + 1e-9]
    y_eval = np.empty((12, t_eval.size), dtype=float)
    y_eval[:, 0] = state.as_array()
    eval_idx = 1
    demand_gen_pu_eval = np.empty(t_eval.size, dtype=float)
    demand_gen_pu_eval[0] = Pe0_gen

    sol = None
    for k in range(load_time_s.size - 1):
        t0 = float(load_time_s[k])
        t1 = float(load_time_s[k + 1])
        Pe_chunk = float(load_gen_pu[k])
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
            demand_gen_pu_eval[eval_idx] = Pe_chunk
            eval_idx += 1
        state = GGOV1State.from_array(sol.y[:, -1])
    while eval_idx < t_eval.size:
        y_eval[:, eval_idx] = sol.sol(min(t_eval[eval_idx], load_time_s[-1]))
        demand_gen_pu_eval[eval_idx] = float(load_gen_pu[-2])
        eval_idx += 1

    # ---- Post-process ----
    omega = y_eval[1]
    Pe_filt = y_eval[2]
    x_kigov = y_eval[3]
    x_ka = y_eval[4]
    x_accel_lag = y_eval[5]
    x_kiload = y_eval[6]
    x_tload = y_eval[7]
    valve = y_eval[9]
    x_turb = y_eval[10]
    P_fuel = y_eval[11]

    freq_hz = omega * 60.0
    speed_rpm = freq_hz * 60.0  # 2-pole machine: 3600 rpm at 60 Hz

    # Turbine mechanical power (vectorized replica of _turbine_power)
    Wf = valve * np.where(p.flag == 1, omega, 1.0)
    if p.Dm < 0:
        Wf = Wf * omega ** p.Dm
    if p.Tb_s > 0:
        dx_turb = (Wf - x_turb) / p.Tb_s
        x_turb_out = x_turb + p.Tc_s * dx_turb
    else:
        x_turb_out = Wf
    Pm_turb = p.Kturb * (x_turb_out - p.Wfnl)
    if p.Dm > 0:
        Pm_turb = Pm_turb - p.Dm * (omega - 1.0)
    Pm_mw = Pm_turb * p.Trate_mw
    Pm_pu_gen = Pm_turb * p.kb

    # Actual Pe including load damping (V&V fix G4)
    Pe_gen = demand_gen_pu_eval * (1.0 + p.alpha_load_damping * (omega - 1.0))
    Pe_mw = Pe_gen * Sn
    Pe_demand_mw = demand_gen_pu_eval * Sn

    # Controller diagnostics
    fsrn = np.empty_like(t_eval)
    fsra = np.empty_like(t_eval)
    fsrt = np.empty_like(t_eval)
    for i in range(t_eval.size):
        n, a, tt, _, _, _ = _compute_controllers(
            omega[i], Pe_filt[i], Pref, valve[i], x_kigov[i], x_kiload[i],
            x_tload[i], x_accel_lag[i], x_ka[i], p,
        )
        fsrn[i], fsra[i], fsrt[i] = n, a, tt
    fsr = np.minimum(np.minimum(fsrn, fsra), fsrt)

    result = GGOV1Result(
        t_s=t_eval, delta_rad=y_eval[0], omega_pu=omega,
        freq_hz=freq_hz, speed_rpm=speed_rpm,
        Pm_mw=Pm_mw, Pm_pu=Pm_pu_gen, Pe_mw=Pe_mw, Pe_pu=Pe_gen,
        Pe_demand_mw=Pe_demand_mw,
        valve_pu=valve, P_fuel_pu=P_fuel,
        fsrn_pu=fsrn, fsra_pu=fsra, fsrt_pu=fsrt, fsr_pu=fsr,
    )

    # ---- Fuel (V&V fix G2) ----
    if dispatch_fn is not None:
        # Corrected mapping: fuel signal -> equivalent POWER load fraction.
        load_frac = np.clip(p.Kturb * (P_fuel - p.Wfnl), 0.0, 1.0)
        disp = dispatch_fn(load_frac)
        result.fuel_kg_s = np.asarray(disp["fuel_kg_s"], dtype=float)
    else:
        # Native calibration: kg/s proportional to the (combustor-lagged)
        # fuel signal.
        result.fuel_kg_s = p.wf_base_kg_s * np.clip(P_fuel, 0.0, None)
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
    """Step electrical demand P_initial -> P_final at t = t_step."""
    if params is None:
        params = GGOV1Params.lm2500_overrides()
    load_time_s = np.array([0.0, t_step_s, t_end_s])
    load_demand_mw = np.array([P_initial_mw, P_final_mw, P_final_mw])
    return simulate_ggov1(
        load_time_s=load_time_s,
        load_demand_mw=load_demand_mw,
        params=params,
        sample_dt_s=sample_dt_s,
    )

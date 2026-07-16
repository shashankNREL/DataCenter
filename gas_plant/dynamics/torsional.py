"""Tier E: PT-generator torsional shaft model + calibrated rainflow / Miner.

Refines Tier C's lumped PT+gen rotor into TWO masses connected by the
ACTUAL physical output shaft:

    PT rotor (IPT+FPT lumped)  --k,c--  Generator rotor

where k and c are the PT-gen coupling shaft's torsional stiffness and
structural damping. This exposes the dominant torsional natural mode
(typically 18-30 Hz for an aero gen-set) that no swing-equation model
can resolve.

What's NOT a physical shaft (and therefore not in this model):
  - The "coupling" between HP rotor and PT rotor in Tier C is the GAS
    PATH (combustion gas flowing through turbine stages), not a
    mechanical shaft. No torsional fatigue mode there.
  - The HP rotor and its accessory gearbox are treated as input
    forcing only (their dynamics come from Tier C's `Pm_hp_mw`).

For a more refined 3-mass model (e.g. splitting the generator into rotor
+ exciter rotor connected by a flexible coupling) extend with another
mass-stiffness pair following the same pattern.

How it plugs in:

  1. Run Tier C (`simulate_multishaft`) to get Pe(t) and Pin_pt(t) =
     the power flowing from HP-PT gas-path coupling into the PT rotor.

  2. `compute_shaft_torques()` interpolates these onto a 1 kHz grid and
     integrates the 2-mass torsional ODE.

  3. Output is the time-resolved shaft torque T_pt_gen(t) in kN·m,
     ready for rainflow.

  4. `rainflow_count()` + `miners_damage()` apply ASTM-style 4-point
     stack counting + Basquin S-N + optional Goodman mean-stress
     correction, using calibrated material properties.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from scipy.integrate import solve_ivp
from scipy.interpolate import interp1d


# ---------------------------------------------------------------------------
# Torsional model parameters
# ---------------------------------------------------------------------------

@dataclass
class TorsionalParams:
    """2-mass PT-gen torsional model + calibrated fatigue parameters."""

    # ----- Mass split of Tier-C lumped PT+gen rotor (s, gen MVA base) -----
    # Typical aero gen-set: PT rotor ~ 20-30 % of the PT+gen total inertia
    # (smaller free turbine), generator + flywheel ~ 70-80 %.
    H_pt_only_s: float = 0.5
    H_gen_s: float = 2.0

    # ----- Torsional natural frequency (Hz) — used to back-solve stiffness -----
    f_torsion_hz: float = 22.0   # typical aero gen-set: 18-30 Hz

    # ----- Modal damping (ratio of critical) -----
    zeta_torsion: float = 0.010  # 1.0% — typical structural

    # ----- Machine base -----
    Sn_mva: float = 23.0
    omega_0_rad_s: float = field(default=2.0 * np.pi * 60.0, init=False)

    # ----- Shaft section (for torque-to-stress conversion) -----
    # PT-gen coupling shaft typical for a 22 MW aero: ~150 mm OD steel
    shaft_diameter_mm: float = 150.0  # outer diameter
    shaft_inner_mm: float = 50.0      # inner diameter (hollow)
    shaft_steel: str = "AISI 4340"    # high-strength shaft steel

    # ----- Fatigue model (Basquin / Miner / Goodman) -----
    m_fatigue: float = 9.0          # high-strength steel torsion: 9-12
    N_ref: float = 1e8              # reference cycles at Sa_ref (endurance regime)
    Sa_ref_mpa: Optional[float] = None  # if None, auto-compute as 0.3 * UTS
    ultimate_strength_mpa: float = 1000.0  # AISI 4340 typical UTS
    yield_strength_mpa: float = 800.0      # AISI 4340 typical yield

    # ----- Cached derived -----
    k_pu: float = field(init=False)  # shaft stiffness (pu torque / rad)
    c_pu: float = field(init=False)

    def __post_init__(self) -> None:
        M_pt = 2 * self.H_pt_only_s
        M_gen = 2 * self.H_gen_s
        # Reduced inertia for the torsional mode: I_red = I_pt*I_gen/(I_pt+I_gen)
        I_red = (M_pt * M_gen) / (M_pt + M_gen)
        omega_n = 2 * np.pi * self.f_torsion_hz
        # Per-unit stiffness derivation (CORRECTED in v2):
        #   System ODE in pu uses dtheta/dt = (omega_pt - omega_gen) * omega_0
        #   and M * d(omega)/dt = tau_pu. Combining gives
        #   d^2(theta)/dt^2 = omega_0 * tau_pu / M.
        #   For torsional spring tau = -k_pu * theta:
        #   d^2(theta)/dt^2 = -(omega_0 * k_pu / I_red) * theta
        #   so omega_n^2 = omega_0 * k_pu / I_red, hence
        #   k_pu = omega_n^2 * I_red / omega_0
        # v1 used k_pu = omega_n^2 * I_red (no /omega_0 factor), which made
        # the actual natural frequency sqrt(omega_0) ≈ 19.4x too high
        # (427 Hz instead of 22 Hz) and produced a very stiff integrator.
        self.k_pu = omega_n**2 * I_red / self.omega_0_rad_s
        # Damping: with derivation c_pu = 2 * zeta * omega_n_phys * I_red_pu
        # (the factor of omega_0 cancels, as verified by eigenvalue check)
        self.c_pu = 2 * self.zeta_torsion * omega_n * I_red

        if self.Sa_ref_mpa is None:
            # Endurance shear stress ~ 0.3 UTS as Basquin reference amplitude
            self.Sa_ref_mpa = 0.30 * self.ultimate_strength_mpa


# ---------------------------------------------------------------------------
# Shaft section properties
# ---------------------------------------------------------------------------

def section_modulus_torsion_m3(diameter_mm: float, inner_mm: float = 0.0) -> float:
    """Polar section modulus W_t for a hollow circular shaft (m^3).

    Shear stress from torque: tau = T (N·m) / W_t (m^3).
    """
    D = diameter_mm * 1e-3
    d = inner_mm * 1e-3
    return (np.pi / 16.0) * (D**4 - d**4) / D


# ---------------------------------------------------------------------------
# Compute shaft torque from Tier C trajectory
# ---------------------------------------------------------------------------

def compute_shaft_torques(
    tierC_result,           # MultishaftResult
    params: TorsionalParams = TorsionalParams(),
    sample_rate_hz: float = 1000.0,
    decimate_to_hz: Optional[float] = None,
) -> dict:
    """Solve the 2-mass PT-gen torsional ODE driven by Tier C trajectories.

    Inputs:
      tierC_result: a `MultishaftResult` from simulate_multishaft()
      params:       torsional parameters
      sample_rate_hz: solver / output sample rate. 1 kHz default captures
                      modes up to ~250 Hz with margin.
      decimate_to_hz: if set, downsample the output to this rate.

    Returns dict with:
      t            shaft-torque time array (s)
      omega_pt_pu  PT rotor speed (pu of 3600 rpm)
      omega_gen_pu generator rotor speed (pu of 3600 rpm)
      T_shaft_pu   shaft torque (pu on gen MVA base)
      T_shaft_kNm  shaft torque in physical units
    """
    p = params

    # ---- Forcing from Tier C ----
    # Pin_pt: power delivered from HP-PT gas-path coupling into the PT rotor.
    # Pe:     electrical load power.
    # COM speed (omega_avg = Tier-C's lumped PT-gen rotor speed) is treated as
    # an EXTERNAL forcing — it's already a self-consistent state in the Tier-C
    # solution, so importing it here decouples our local torsional dynamics
    # from any small forcing inconsistency that would otherwise drift the
    # common-mode (Tier E v2 fix).
    Sn = p.Sn_mva
    t_in = tierC_result.t_s
    Pin_pt_pu = tierC_result.Pm_pt_mw / Sn
    Pe_pu = tierC_result.Pe_mw / Sn
    omega_avg = tierC_result.omega_pt_pu  # Tier-C lumped rotor speed = COM

    f_Pin_pt = interp1d(t_in, Pin_pt_pu, kind="linear",
                        bounds_error=False, fill_value=(Pin_pt_pu[0], Pin_pt_pu[-1]))
    f_Pe = interp1d(t_in, Pe_pu, kind="linear",
                    bounds_error=False, fill_value=(Pe_pu[0], Pe_pu[-1]))
    f_omega_avg = interp1d(t_in, omega_avg, kind="linear",
                           bounds_error=False, fill_value=(omega_avg[0], omega_avg[-1]))

    # ---- Differential-mode state vector (Tier E v2) ----
    # State: [theta_twist, omega_diff]
    #   theta_twist = theta_pt - theta_gen     (rad)
    #   omega_diff  = omega_pt - omega_gen     (pu)
    # The per-rotor speeds are RECONSTRUCTED from the externally-imposed COM
    # speed and the local omega_diff:
    #   omega_pt  = omega_avg + (M_gen/M_tot) * omega_diff
    #   omega_gen = omega_avg - (M_pt /M_tot) * omega_diff
    # This guarantees the common mode CANNOT drift independently — it is
    # always equal to Tier C's omega_pt by construction.
    #
    # Differential equation derived from
    #     M_pt * d(omega_pt) /dt = tau_pt_in - T_shaft
    #     M_gen* d(omega_gen)/dt = T_shaft - tau_e_gen
    # Subtracting:
    #     d(omega_diff)/dt = tau_pt_in/M_pt + tau_e_gen/M_gen - T_shaft/I_red
    # where I_red = M_pt*M_gen/(M_pt+M_gen).
    # In SS: T_shaft_SS = I_red*(tau_pt/M_pt + tau_e/M_gen), independent of
    # any small persistent (tau_pt - tau_e) imbalance — fixes the drift.

    M_pt = 2 * p.H_pt_only_s
    M_gen = 2 * p.H_gen_s
    M_tot = M_pt + M_gen
    I_red = (M_pt * M_gen) / M_tot
    frac_pt = M_pt / M_tot
    frac_gen = M_gen / M_tot

    # Initial conditions
    Pe0 = Pe_pu[0]
    Pin0 = Pin_pt_pu[0]
    # SS reduced-mass torque (handles small Pe0 != Pin0 cleanly)
    tau_e0 = Pe0 / max(omega_avg[0], 0.1)
    tau_pt0 = Pin0 / max(omega_avg[0], 0.1)
    T_shaft_0 = I_red * (tau_pt0 / M_pt + tau_e0 / M_gen)
    theta_twist_0 = T_shaft_0 / p.k_pu
    omega_diff_0 = 0.0

    y0 = np.array([theta_twist_0, omega_diff_0], dtype=float)

    def rhs(t, y):
        theta_twist, omega_diff = y

        # Reconstruct per-rotor speeds from COM + diff
        oavg = float(f_omega_avg(t))
        omega_pt = oavg + frac_gen * omega_diff
        omega_gen = oavg - frac_pt * omega_diff

        # Shaft torque (pu)
        T_shaft = p.k_pu * theta_twist + p.c_pu * omega_diff

        # External torques
        Pin = float(f_Pin_pt(t))
        Pe_t = float(f_Pe(t))
        tau_pt_in = Pin / max(omega_pt, 0.1)
        tau_e_gen = Pe_t / max(omega_gen, 0.1)

        # Differential-mode dynamics
        domega_diff = (tau_pt_in / M_pt + tau_e_gen / M_gen) - T_shaft / I_red
        dtheta_twist = omega_diff * p.omega_0_rad_s

        return np.array([dtheta_twist, domega_diff])

    # Integrate at high rate
    t_end = t_in[-1]
    dt = 1.0 / sample_rate_hz
    t_eval = np.arange(0.0, t_end + dt, dt)
    t_eval = t_eval[t_eval <= t_end]
    sol = solve_ivp(
        rhs, (0.0, t_end), y0, method="RK45",
        t_eval=t_eval, max_step=dt * 5,
        rtol=1e-6, atol=1e-9,
    )
    if not sol.success:
        raise RuntimeError(f"Torsional solve failed: {sol.message}")

    theta_twist = sol.y[0]
    omega_diff = sol.y[1]
    # Reconstruct per-rotor speeds for diagnostics
    omega_avg_eval = f_omega_avg(sol.t)
    omega_pt = omega_avg_eval + frac_gen * omega_diff
    omega_gen = omega_avg_eval - frac_pt * omega_diff
    T_shaft_pu = p.k_pu * theta_twist + p.c_pu * omega_diff

    # Convert to kN·m on the PT-gen shaft (at 3600 rpm rated)
    omega_mech_pt_gen = 2 * np.pi * 60.0  # rad/s
    base_torque_Nm = Sn * 1e6 / omega_mech_pt_gen
    T_shaft_kNm = T_shaft_pu * base_torque_Nm / 1000.0

    result = {
        "t": sol.t,
        "omega_pt_pu": omega_pt,
        "omega_gen_pu": omega_gen,
        "theta_twist_rad": theta_twist,
        "T_shaft_pu": T_shaft_pu,
        "T_shaft_kNm": T_shaft_kNm,
    }

    if decimate_to_hz is not None and decimate_to_hz < sample_rate_hz:
        step = int(sample_rate_hz / decimate_to_hz)
        for k in result:
            result[k] = result[k][::step]

    return result


# ---------------------------------------------------------------------------
# Rainflow + Miner with Goodman mean-stress correction
# ---------------------------------------------------------------------------

def _compress_consecutive_duplicates(x: np.ndarray) -> tuple:
    if x.size == 0:
        return x, np.array([], dtype=int)
    keep = np.ones_like(x, dtype=bool)
    keep[1:] = x[1:] != x[:-1]
    return x[keep], np.nonzero(keep)[0]


def detrend_rolling_median(
    signal: np.ndarray,
    times: np.ndarray,
    window_s: float = 5.0,
) -> tuple:
    """Subtract a rolling-median trend from `signal`, returning (residual, trend).

    Useful for separating bulk transients (operating-point shifts) from the
    high-frequency oscillations that rainflow should count. The trend
    captures slow changes in the mean; the residual contains only zero-mean
    oscillations suitable for ASTM E1049 rainflow.

    For separate low-cycle-fatigue accounting on the bulk transient, count
    extreme-value excursions of `trend` directly (each excursion is a low-
    cycle event), not via rainflow.

    Args:
      signal:   1-D time series (e.g. T_shaft_kNm)
      times:    1-D time array (same shape)
      window_s: rolling window in seconds. Should be >> 1/f_torsion so it
                doesn't filter out the mode but << bulk transient duration.
                For a 22 Hz mode and seconds-long transients, 5 s is a good
                default (passes 22 Hz at full amplitude, rejects ~0.2 Hz and below).

    Returns:
      (residual, trend) — same shape as input. residual = signal - trend.
    """
    signal = np.asarray(signal, dtype=float)
    times = np.asarray(times, dtype=float)
    if signal.shape != times.shape:
        raise ValueError("signal and times must be the same shape")
    if signal.size < 4:
        return signal.copy(), np.zeros_like(signal)

    # Estimate sample rate from times, derive window in samples
    dt = float(np.median(np.diff(times)))
    win = max(3, int(round(window_s / dt)))
    if win % 2 == 0:
        win += 1   # odd window so it has a center

    # Pandas rolling median is fast and robust to spikes
    s = pd.Series(signal)
    trend = s.rolling(window=win, center=True, min_periods=1).median().values
    residual = signal - trend
    return residual, trend


def rainflow_count(signal: np.ndarray, times: Optional[np.ndarray] = None) -> pd.DataFrame:
    """ASTM-style 4-point stack rainflow on a torque/stress signal.

    Returns DataFrame with columns: range, amplitude, mean, count, t_close.

    NOTE on non-stationary signals: rainflow is designed for stationary
    processes (signals oscillating around a constant mean). For signals
    whose mean shifts over time (e.g. T_shaft during a load step or
    rejection), the bulk shift is NOT counted as a damaging cycle — only
    the high-frequency oscillations around it are. Use
    `detrend_rolling_median()` first to separate the bulk trend (which
    should be analyzed as discrete low-cycle events) from the high-cycle
    residual (which feeds correctly into rainflow + Miner).
    """
    x = np.asarray(signal, dtype=float)
    if times is None:
        times = np.arange(x.size, dtype=float)
    times = np.asarray(times, dtype=float)

    x_c, idx_keep = _compress_consecutive_duplicates(x)
    t_c = times[idx_keep] if x.size == times.size else times

    if x_c.size < 3:
        return pd.DataFrame(columns=["range", "amplitude", "mean", "count", "t_close"])

    idx_tp = [0]
    for i in range(1, x_c.size - 1):
        prev_, cur_, next_ = x_c[i - 1], x_c[i], x_c[i + 1]
        if (cur_ > prev_ and cur_ >= next_) or (cur_ < prev_ and cur_ <= next_):
            idx_tp.append(i)
    idx_tp.append(x_c.size - 1)
    tp = x_c[idx_tp]
    tt = t_c[idx_tp]

    stack_vals, stack_times, cycles = [], [], []
    for v, t in zip(tp, tt):
        stack_vals.append(v)
        stack_times.append(t)
        while len(stack_vals) >= 3:
            s0 = abs(stack_vals[-2] - stack_vals[-3])
            s1 = abs(stack_vals[-1] - stack_vals[-2])
            if s1 >= s0:
                rng = s0
                amp = 0.5 * rng
                mn = 0.5 * (stack_vals[-3] + stack_vals[-2])
                cnt = 1.0 if len(stack_vals) > 3 else 0.5
                t_close = stack_times[-2]
                cycles.append((rng, amp, mn, cnt, t_close))
                del stack_vals[-3:-1]
                del stack_times[-3:-1]
            else:
                break

    for i in range(len(stack_vals) - 1):
        rng = abs(stack_vals[i + 1] - stack_vals[i])
        amp = 0.5 * rng
        mn = 0.5 * (stack_vals[i + 1] + stack_vals[i])
        cycles.append((rng, amp, mn, 0.5, stack_times[i + 1]))

    out = pd.DataFrame(cycles, columns=["range", "amplitude", "mean", "count", "t_close"])
    return out.sort_values("t_close").reset_index(drop=True)


def miners_damage(
    cycles_df: pd.DataFrame,
    params: TorsionalParams,
    torque_to_stress_Pa_per_kNm: Optional[float] = None,
    use_goodman: bool = True,
) -> pd.DataFrame:
    """Apply Basquin S-N + (optional Goodman) mean-stress correction.

    Inputs:
      cycles_df:    output of rainflow_count() on a torque history (kN·m)
      params:       TorsionalParams (provides material, shaft geometry,
                    Basquin slope, reference cycles, reference amplitude)
      torque_to_stress_Pa_per_kNm: if provided, use this conversion.
                    Otherwise computed from shaft section modulus.
      use_goodman:  apply Goodman mean-stress correction
                    Sa_eq = Sa / (1 - Sm/Su_shear)

    Returns the input DataFrame with added columns:
      Sa_mpa, Sm_mpa, Sa_eq_mpa, Nf, d_i, D_cum
    """
    p = params
    out = cycles_df.copy()
    if len(out) == 0:
        return out.assign(Sa_mpa=[], Sm_mpa=[], Sa_eq_mpa=[], Nf=[], d_i=[], D_cum=[])

    if torque_to_stress_Pa_per_kNm is None:
        # tau (Pa) = T (N·m) / W_t (m^3) = T (kN·m) * 1000 / W_t
        W_t = section_modulus_torsion_m3(p.shaft_diameter_mm, p.shaft_inner_mm)
        torque_to_stress_Pa_per_kNm = 1000.0 / W_t  # Pa per kN·m

    Sa_pa = out["amplitude"].values * torque_to_stress_Pa_per_kNm
    Sm_pa = out["mean"].values * torque_to_stress_Pa_per_kNm
    Sa_mpa = Sa_pa / 1e6
    Sm_mpa = Sm_pa / 1e6

    # Ultimate SHEAR strength ≈ 0.6 × ultimate tensile (typical for steel)
    Su_shear_mpa = 0.6 * p.ultimate_strength_mpa

    if use_goodman:
        denom = 1.0 - np.clip(Sm_mpa / Su_shear_mpa, -10, 0.99)
        Sa_eq_mpa = np.where(denom > 0, Sa_mpa / denom, Sa_mpa * 100)
    else:
        Sa_eq_mpa = Sa_mpa

    Sa_eff = np.clip(Sa_eq_mpa, 1e-6, None)
    Nf = p.N_ref * (p.Sa_ref_mpa / Sa_eff) ** p.m_fatigue
    d_i = out["count"].values / Nf

    out["Sa_mpa"] = Sa_mpa
    out["Sm_mpa"] = Sm_mpa
    out["Sa_eq_mpa"] = Sa_eq_mpa
    out["Nf"] = Nf
    out["d_i"] = d_i
    out["D_cum"] = np.cumsum(d_i)
    return out

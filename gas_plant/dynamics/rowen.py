"""Rowen-form single-shaft gas-turbine + governor model (Hannett Fig. 1/2).

Implements the simplified heavy-duty gas-turbine model of Rowen (1983), in
the parameterization used by Hannett & Khan (1993) for the Alaskan units
("Table 1/2" parameters, vendored in papers/Hannett_table1.txt). Used ONLY
for V&V cross-comparison against the GGOV1 implementation (Phase 3) — the
production LM2500 model remains GGOV1.

Block structure (speed loop; per-unit on the TURBINE MW base):

  speed err ->  W(Xs+1)/(Ys+Z)  -> clip[MinVce, MaxVce] -> valve positioner
                (governor)                                  a/(bs+c)
  -> fuel system 1/(tf s+1) -> Wf = (K3*vce + K6)*N -> lag TCD
  -> torque f2 = af2 + bf2*Wf + cf2*(1-N) -> Pm = f2*N -> swing eq

Modeling notes / deviations (documented for V&V):
  - The temperature-control loop and radiation-shield/thermocouple path are
    OMITTED: per Hannett & Khan, during the governor tests "the reference
    value is higher than the thermocouple output", i.e. the temperature
    controller sits at its maximum limit and never passes the low-value
    select. The LVG therefore reduces to the governor branch for these
    scenarios.
  - The small digital-governor transport delay ECR (0.01 s) is folded into
    the fuel-system lag (it is 40x smaller than tf).
  - Torque coefficient cf2: Rowen (1983) and the unit rows of Hannett's
    table use 0.5; the vendored "Typical Gas" row shows 1.5, which is
    inconsistent with f2(Wf=1, N=1) = 1.0 and is treated as a transcription
    artifact. cf2 = 0.5 is used (f2 = 1.3*Wf - 0.299 + 0.5*(1-N)).
  - Steady state requires f2(Wf0, 1) = Pm0 with Wf0 = K3*Pm0 + K6; the
    typical-gas coefficients satisfy this identically
    (bf2*K3 = 1.3*0.77 = 1.001, af2 + bf2*K6 = -0.299 + 0.299 = 0).

States: omega, x_gov, vce_pos (valve positioner), wf (fuel system), x_tcd.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from scipy.integrate import solve_ivp


@dataclass
class RowenParams:
    """Hannett & Khan (1993) 'Typical Gas' parameters (Table in papers/).

    Per-unit base: turbine MW rating (`Trate_mw`). The swing equation is on
    the generator base `Sn_mva` with conversion kb = Trate/Sn, mirroring the
    GGOV1 convention.
    """

    Sn_mva: float = 23.0
    Trate_mw: float = 22.0
    H_s: float = 2.8           # inertia (s, gen base)
    D_pu: float = 0.0
    alpha_load_damping: float = 0.0   # keep 0 for literature protocols

    # Governor W(Xs+1)/(Ys+Z); droop = Z/W
    W: float = 25.0
    X: float = 0.0
    Y: float = 0.05
    Z: float = 1.0
    max_vce: float = 1.5
    min_vce: float = -0.1

    # Fuel path
    K3: float = 0.77           # fuel valve gain; K6 = 1 - K3 = no-load fuel
    a_vp: float = 1.0          # valve positioner a/(bs+c)
    b_vp: float = 0.05
    c_vp: float = 1.0
    tf_s: float = 0.4          # fuel system time constant
    T_cd_s: float = 0.1        # compressor discharge lag

    # Torque function f2 = af2 + bf2*Wf + cf2*(1 - N)
    af2: float = -0.299
    bf2: float = 1.3
    cf2: float = 0.5

    rtol: float = 1e-8
    atol: float = 1e-10
    max_step_s: float = 0.25

    omega_0_rad_s: float = field(default=2.0 * np.pi * 60.0, init=False)
    M_pu_s: float = field(init=False)
    kb: float = field(init=False)
    K6: float = field(init=False)

    def __post_init__(self) -> None:
        self.M_pu_s = 2.0 * self.H_s
        self.kb = self.Trate_mw / self.Sn_mva
        self.K6 = 1.0 - self.K3

    @property
    def droop(self) -> float:
        return self.Z / self.W


@dataclass
class RowenResult:
    t_s: np.ndarray
    omega_pu: np.ndarray
    freq_hz: np.ndarray
    Pm_mw: np.ndarray
    Pe_mw: np.ndarray
    vce_pu: np.ndarray
    wf_pu: np.ndarray

    def as_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame({
            "t_s": self.t_s, "omega_pu": self.omega_pu,
            "freq_hz": self.freq_hz, "Pm_mw": self.Pm_mw,
            "Pe_mw": self.Pe_mw, "vce_pu": self.vce_pu, "wf_pu": self.wf_pu,
        })


def simulate_rowen(
    load_time_s: np.ndarray,
    load_demand_mw: np.ndarray,
    params: RowenParams = RowenParams(),
    sample_dt_s: float = 0.01,
) -> RowenResult:
    """Run the Rowen model over a ZOH load profile (chunked integration)."""
    p = params
    load_time_s = np.asarray(load_time_s, dtype=float)
    load_gen_pu = np.asarray(load_demand_mw, dtype=float) / p.Sn_mva
    if load_time_s[0] > 0:
        load_time_s = np.concatenate([[0.0], load_time_s])
        load_gen_pu = np.concatenate([[load_gen_pu[0]], load_gen_pu])

    # ---- Initial steady state ----
    Pe0_turb = float(load_gen_pu[0]) / p.kb   # = Pm0 = Vce0 (paper: Vce pu
    #                                            equals Pm pu at steady state)
    vce0 = Pe0_turb
    wf0 = p.K3 * vce0 + p.K6
    # Governor at SS: x_gov = Z*... out = vce0 -> err0 = vce0 * Z / W
    err0 = vce0 * p.Z / p.W
    omega_ref = 1.0 + err0   # droop reference absorbing the initial load

    # states: [omega, x_gov, vce_pos, wf, x_tcd]
    y0 = np.array([1.0, vce0, vce0, wf0, wf0])

    def rhs_factory(Pe_gen_dem):
        def rhs(t, y):
            omega, x_gov, vce_pos, wf, x_tcd = y
            Pe_gen = Pe_gen_dem * (1.0 + p.alpha_load_damping * (omega - 1.0))

            err = omega_ref - omega
            # W(Xs+1)/(Ys+Z): state x_gov with dx = (W*err - Z*x)/Y,
            # output = x + (X/Y)*(W*err - Z*x)  (lead term; X=0 -> out=x)
            dx_gov = (p.W * err - p.Z * x_gov) / p.Y
            gov_out = x_gov + p.X * dx_gov   # lead term of W(Xs+1)/(Ys+Z)
            vce_cmd = float(np.clip(gov_out, p.min_vce, p.max_vce))

            # valve positioner a/(bs+c)
            dvce_pos = (p.a_vp * vce_cmd - p.c_vp * vce_pos) / p.b_vp
            # fuel system lag; fuel demand scaled by K3 with K6 offset, x speed
            wf_cmd = (p.K3 * vce_pos + p.K6) * omega
            dwf = (wf_cmd - wf) / p.tf_s
            # compressor discharge lag
            dx_tcd = (wf - x_tcd) / p.T_cd_s

            f2 = p.af2 + p.bf2 * x_tcd + p.cf2 * (1.0 - omega)
            Pm_turb = f2 * omega
            domega = (p.kb * Pm_turb - Pe_gen - p.D_pu * (omega - 1.0)) / p.M_pu_s
            return np.array([domega, dx_gov, dvce_pos, dwf, dx_tcd])
        return rhs

    t_eval = np.arange(0.0, load_time_s[-1] + sample_dt_s, sample_dt_s)
    t_eval = t_eval[t_eval <= load_time_s[-1] + 1e-9]
    Y = np.empty((5, t_eval.size))
    Y[:, 0] = y0
    dem = np.empty(t_eval.size)
    dem[0] = load_gen_pu[0]
    idx = 1
    state = y0
    sol = None
    for k in range(load_time_s.size - 1):
        t0, t1 = float(load_time_s[k]), float(load_time_s[k + 1])
        rhs = rhs_factory(float(load_gen_pu[k]))
        sol = solve_ivp(rhs, (t0, t1), state, method="RK45",
                        max_step=p.max_step_s, rtol=p.rtol, atol=p.atol,
                        dense_output=True)
        if not sol.success:
            raise RuntimeError(sol.message)
        while idx < t_eval.size and t_eval[idx] <= t1 + 1e-9:
            Y[:, idx] = sol.sol(t_eval[idx])
            dem[idx] = float(load_gen_pu[k])
            idx += 1
        state = sol.y[:, -1]
    while idx < t_eval.size:
        Y[:, idx] = sol.sol(min(t_eval[idx], load_time_s[-1]))
        dem[idx] = float(load_gen_pu[-2])
        idx += 1

    omega = Y[0]
    x_tcd = Y[4]
    f2 = p.af2 + p.bf2 * x_tcd + p.cf2 * (1.0 - omega)
    Pm_mw = f2 * omega * p.Trate_mw
    Pe_mw = dem * (1.0 + p.alpha_load_damping * (omega - 1.0)) * p.Sn_mva
    return RowenResult(
        t_s=t_eval, omega_pu=omega, freq_hz=omega * 60.0,
        Pm_mw=Pm_mw, Pe_mw=Pe_mw, vce_pu=Y[2], wf_pu=Y[3],
    )

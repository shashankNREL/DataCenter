"""V&V Phase 1 — small-signal verification of the GGOV1 implementation.

Checks, at several operating points:
  1. The steady-state IC really is an equilibrium (||RHS|| ~ 0).
  2. All eigenvalues of the numerically linearized closed-loop system have
     negative real parts (except the rotor-angle zero mode, which is
     marginal by construction — delta does not feed back in an islanded
     constant-power case).
  3. The droop DC gain identity: a sustained load step ends at
     delta_w = -R * delta_Pe (turbine base), i.e. dPm/d(delta_w) = -1/R.
  4. The torsional 2-mass model's undamped natural frequency equals the
     design f_torsion (closed form from k_pu, I_red).

Run:  pixi run python tools/vv/smallsignal.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gas_plant.dynamics.ggov1 import (   # noqa: E402
    GGOV1Params, _initial_state_for_load, _pref_for_rselect, _rhs_factory,
    simulate_ggov1,
)
from gas_plant.dynamics.torsional import TorsionalParams  # noqa: E402


def linearize(rhs, y0: np.ndarray, eps: float = 1e-7) -> np.ndarray:
    n = y0.size
    J = np.zeros((n, n))
    f0 = rhs(0.0, y0)
    for i in range(n):
        dy = np.zeros(n)
        dy[i] = eps * max(1.0, abs(y0[i]))
        J[:, i] = (rhs(0.0, y0 + dy) - f0) / dy[i]
    return J


STATE_NAMES = ["delta", "omega", "Pe_filt", "x_kigov", "x_ka", "x_accel_lag",
               "x_kiload", "x_tload", "x_tsab", "valve", "x_turb", "P_fuel"]


def check_operating_point(p: GGOV1Params, Pe_mw: float) -> dict:
    Pe_gen = Pe_mw / p.Sn_mva
    Pe_turb = Pe_gen / p.kb
    st = _initial_state_for_load(Pe_turb, p)
    Pref = _pref_for_rselect(Pe_turb, p)
    rhs = _rhs_factory(p, Pref, Pe_gen)
    y0 = st.as_array()

    resid = np.abs(rhs(0.0, y0))
    J = linearize(rhs, y0)
    eig = np.linalg.eigvals(J)
    # delta is a pure integrator of (omega - 1) with no feedback here ->
    # one structural zero eigenvalue; all others must be strictly stable.
    eig_sorted = eig[np.argsort(-eig.real)]
    n_zero = int(np.sum(np.abs(eig.real) < 1e-9))
    max_re_nonzero = max(e.real for e in eig if abs(e.real) >= 1e-9)
    return {
        "Pe_mw": Pe_mw,
        "resid_max": float(resid.max()),
        "resid_arg": STATE_NAMES[int(np.argmax(resid))],
        "n_zero_eig": n_zero,
        "max_re_nonzero": float(max_re_nonzero),
        "eig": eig_sorted,
    }


def droop_dc_gain(p: GGOV1Params, P0_mw: float, P1_mw: float) -> dict:
    res = simulate_ggov1(
        np.array([0.0, 5.0, 200.0]), np.array([P0_mw, P1_mw, P1_mw]),
        params=p, sample_dt_s=0.5,
    )
    dw = res.omega_pu[-1] - 1.0
    dPe_turb = (res.Pe_mw[-1] - res.Pe_mw[0]) / p.Trate_mw  # actual damped Pe
    R_measured = -dw / dPe_turb
    return {"R_measured": R_measured, "R_nominal": p.R,
            "err_pct": 100 * (R_measured - p.R) / p.R}


def torsional_frequency(tp: TorsionalParams) -> dict:
    M_pt = 2 * tp.H_pt_only_s
    M_gen = 2 * tp.H_gen_s
    I_red = M_pt * M_gen / (M_pt + M_gen)
    # From the pu dynamics: theta' = w0 * w_diff ; I_red * w_diff' = -k*theta
    # => omega_n = sqrt(w0 * k / I_red)
    omega_n = np.sqrt(tp.omega_0_rad_s * tp.k_pu / I_red)
    return {"f_design_hz": tp.f_torsion_hz, "f_actual_hz": omega_n / (2 * np.pi)}


def main() -> int:
    ok = True
    print("=== GGOV1 small-signal verification ===")
    p = GGOV1Params.lm2500_overrides()
    for Pe_mw in [5.0, 11.5, 15.0, 20.0, 21.9]:
        r = check_operating_point(p, Pe_mw)
        line_ok = (r["resid_max"] < 1e-9 and r["n_zero_eig"] == 1
                   and r["max_re_nonzero"] < -1e-3)
        ok &= line_ok
        print(f"Pe={Pe_mw:5.1f} MW  ||RHS||={r['resid_max']:.2e} (at {r['resid_arg']})"
              f"  zero-modes={r['n_zero_eig']}  max Re(eig)!=0 = {r['max_re_nonzero']:.4f}"
              f"  {'ok' if line_ok else 'FAIL'}")

    print("\n=== Droop DC-gain identity (rselect=1) ===")
    d = droop_dc_gain(p, 11.5, 14.0)
    droop_ok = abs(d["err_pct"]) < 1.0
    ok &= droop_ok
    print(f"R measured = {d['R_measured']:.5f} vs nominal {d['R_nominal']:.5f} "
          f"({d['err_pct']:+.3f} %)  {'ok' if droop_ok else 'FAIL'}")

    print("\n=== Isochronous DC check (rselect=0) ===")
    p_iso = GGOV1Params.lm2500_overrides(rselect=0)
    res = simulate_ggov1(np.array([0.0, 5.0, 200.0]),
                         np.array([11.5, 14.0, 14.0]),
                         params=p_iso, sample_dt_s=0.5)
    df_mhz = abs(res.freq_hz[-1] - 60.0) * 1000
    iso_ok = df_mhz < 0.1
    ok &= iso_ok
    print(f"final |df| = {df_mhz:.4f} mHz  {'ok' if iso_ok else 'FAIL'}")

    print("\n=== Torsional natural frequency (closed form) ===")
    t = torsional_frequency(TorsionalParams())
    tors_ok = abs(t["f_actual_hz"] - t["f_design_hz"]) < 0.01
    ok &= tors_ok
    print(f"design {t['f_design_hz']:.2f} Hz vs actual {t['f_actual_hz']:.4f} Hz  "
          f"{'ok' if tors_ok else 'FAIL'}")

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

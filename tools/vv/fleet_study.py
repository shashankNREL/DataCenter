"""V&V follow-up — fleet study on the MIT SuperCloud worst-case trace.

Questions answered (see docs/ai_workload_review.md §8/§10):
  1. Frequency compliance vs fleet size N (aggregated-equivalent machine:
     Sn = N*23 MVA, Trate = N*22 MW, equal droop sharing on a common bus).
  2. PER-SHAFT torsional fatigue vs N: each unit carries demand/N, so the
     per-shaft torque oscillation amplitude scales ~1/N and Basquin damage
     per cycle ~ (1/N)^m  (m = 9 HCF, m = 4 LCF). The empirical N=3 vs N=4
     segment ratio checks that law.

Per-unit fatigue construction: the aggregated Tier C result carries FLEET
MW; each unit's shaft sees 1/N of Pm_pt and Pe at the same speeds, so we
scale the power columns by 1/N and run Tier E with the single-unit
TorsionalParams (Sn = 23).

Run:  pixi run python tools/vv/fleet_study.py          (~15 min: one full
      2-h Tier C run for N=4 plus two 15-min segment runs)
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from gas_plant.dynamics.ggov1 import GGOV1Params  # noqa: E402
from gas_plant.dynamics.multishaft import (  # noqa: E402
    MultishaftParams, simulate_multishaft,
)
from gas_plant.dynamics.torsional import (  # noqa: E402
    TorsionalParams, compute_shaft_torques, detrend_rolling_median,
    rainflow_count, miners_damage,
)


def build_trace():
    """Corrected facility trace (per-GPU ZOH, additive PUE) — same as the
    notebook, docs/ai_workload_review.md §1.2."""
    nv = pd.read_parquet(ROOT / "data/nvidia_smi_first_1gb.parquet")
    nv = nv.sort_values("timestamp")
    ts = nv.timestamp.values
    bins = np.arange(ts.min(), ts.max() + 60, 60.0)
    counts, _ = np.histogram(ts, bins=bins)
    ws = np.convolve(counts, np.ones(int(2 * 3600 / 60), dtype=int), mode="valid")
    t0 = float(bins[int(np.argmax(ws))])
    sub = nv[(ts >= t0) & (ts < t0 + 7200)].copy()
    sub["slot"] = np.floor((sub.timestamp - t0) * 10).astype(np.int64)
    n = 72000
    idx = np.arange(n)
    B = np.zeros(n)
    for _, g in sub.groupby(["Node", "gpu_index"]):
        v = pd.Series(g.power_draw_W.values, index=g.slot.values)
        v = v[~v.index.duplicated(keep="last")]
        B += v.reindex(idx).ffill().fillna(0.0).values
    PUE = 1.25
    s = 18e6 / (B.max() + (PUE - 1) * B.mean())
    p_mw = (B * s + (PUE - 1) * B.mean() * s) / 1e6
    return idx / 10.0, p_mw


def run_fleet(t, p_mw, N, sample_dt_s=0.02):
    g = GGOV1Params.lm2500_overrides(Sn_mva=23.0 * N, Trate_mw=22.0 * N)
    return simulate_multishaft(t, p_mw, params=MultishaftParams(ggov1=g),
                               sample_dt_s=sample_dt_s)


def per_shaft_fatigue(r, N, fs_hz=500.0):
    """Tier E on ONE unit's shaft: scale fleet powers by 1/N."""
    r1 = copy.copy(r)
    r1.Pm_pt_mw = r.Pm_pt_mw / N
    r1.Pe_mw = r.Pe_mw / N
    tor = TorsionalParams()          # single-unit shaft, Sn = 23
    shaft = compute_shaft_torques(r1, params=tor, sample_rate_hz=fs_hz)
    T, t_sh = shaft["T_shaft_kNm"], shaft["t"]
    res, trend = detrend_rolling_median(T, t_sh, window_s=5.0)
    hcf = miners_damage(rainflow_count(res, t_sh), tor, use_goodman=True)
    lcf_par = TorsionalParams(m_fatigue=4.0, N_ref=1e4, Sa_ref_mpa=500.0)
    lcf = miners_damage(rainflow_count(trend, t_sh), lcf_par, use_goodman=True)
    return {
        "T_range_kNm": (float(T.min()), float(T.max())),
        "D_hcf": float(hcf["d_i"].sum()),
        "D_lcf": float(lcf["d_i"].sum()),
        "window_s": float(t_sh[-1]),
    }


def report(tag, f, dt, fat=None):
    oob = ((f < 59.4) | (f > 60.6)).sum() * dt
    trip = (f < 57.8).sum() * dt
    line = (f"{tag:28s} f {f.min():7.3f}-{f.max():7.3f} Hz  "
            f"<57.8Hz {trip:7.1f} s  oob {oob:7.1f} s")
    if fat:
        D = fat["D_hcf"] + fat["D_lcf"]
        yrs = fat["window_s"] / (365.25 * 24 * 3600)
        line += (f"  | per-shaft T {fat['T_range_kNm'][0]:.1f}.."
                 f"{fat['T_range_kNm'][1]:.1f} kNm  D_HCF {fat['D_hcf']:.2e}"
                 f"  D_LCF {fat['D_lcf']:.2e}  yrs-to-D=1 {yrs/D:.2e}")
    print(line, flush=True)


def main():
    t, p_mw = build_trace()
    print(f"trace: {p_mw.min():.1f}-{p_mw.max():.1f} MW, "
          f"worst step {np.diff(p_mw).min():.1f}/+{np.diff(p_mw).max():.1f} MW\n")

    # ---- segment (15 min around worst step): N=3 vs N=4 fatigue scaling ----
    i_ev = int(np.argmax(np.abs(np.diff(p_mw))))
    lo = max(0, i_ev - 4500)
    hi = min(t.size, lo + 9000)
    tseg, pseg = t[lo:hi] - t[lo], p_mw[lo:hi]
    print("== 15-min worst segment (per-shaft fatigue, compliant N only) ==")
    seg = {}
    for N in [3, 4]:
        r = run_fleet(tseg, pseg, N)
        fat = per_shaft_fatigue(r, N)
        seg[N] = fat
        report(f"N={N} (segment)", r.freq_hz, 0.02, fat)
    for k in ["D_hcf", "D_lcf"]:
        m = 9.0 if k == "D_hcf" else 4.0
        ratio = seg[3][k] / max(seg[4][k], 1e-300)
        print(f"  {k} ratio N=3/N=4: {ratio:9.1f}  "
              f"(Basquin prediction (4/3)^{m:.0f} = {(4/3)**m:.1f})")

    # ---- full 2 h, N=4 (the unassisted-compliant fleet) ----
    print("\n== full 2 h window ==")
    r4 = run_fleet(t, p_mw, 4)
    fat4 = per_shaft_fatigue(r4, 4)
    report("N=4 (full 2 h)", r4.freq_hz, 0.02, fat4)
    print("\nCompare: N=1 + BESS (notebook §6c): per-shaft D_window = 5.99e-10,"
          "\n         T 16.1..33.5 kNm — same negligible-fatigue class.")


if __name__ == "__main__":
    main()

"""V&V follow-up — fleet study on the MIT SuperCloud worst-case trace.

Questions answered (see docs/ai_workload_review.md §8/§10):
  1. Frequency compliance vs fleet size N (aggregated-equivalent machine:
     Sn = N*23 MVA, Trate = N*22 MW, equal droop sharing on a common bus).
  2. PER-SHAFT torsional fatigue vs N: each unit carries demand/N, so the
     per-shaft torque oscillation amplitude scales ~1/N and Basquin damage
    per cycle ~ (1/N)^m  (m = 9 HCF, m = 4 LCF). The empirical N=3 vs N=4
    segment ratio checks that law. N=2 is also screened, but fatigue is not
    calculated if its frequency trajectory crosses the trip threshold.

Per-unit fatigue construction: the aggregated Tier C result carries FLEET
MW; each unit's shaft sees 1/N of Pm_pt and Pe at the same speeds, so we
scale the power columns by 1/N and run Tier E with the single-unit
TorsionalParams (Sn = 23).

Run:  pixi run vv-fleet                               (~15 min: one full
    2-h Tier C run for N=4 plus three 15-min segment runs)

Outputs:
    docs/figs/fleet_study_n234_worst_segment.png
    docs/figs/fleet_study_n4_full_window.png
    docs/figs/fleet_study_n2_torque_worst_segment.png
    docs/figs/fleet_study_n3_torque_worst_segment.png
    docs/figs/fleet_study_n4_torque_worst_segment.png
    docs/figs/fleet_study_n4_torque_full_window.png
"""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DEFAULT_OUTDIR = ROOT / "docs" / "figs"

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


def per_shaft_torsion(r, N, fs_hz=500.0):
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
        "t_s": t_sh,
        "T_shaft_kNm": T,
        "T_residual_kNm": res,
        "T_trend_kNm": trend,
    }


def per_shaft_fatigue(r, N, fs_hz=500.0):
    return per_shaft_torsion(r, N, fs_hz)


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


def finish_plot(fig, out_path, dpi):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi)
    plt.close(fig)
    return out_path


def setup_frequency_axis(ax):
    ax.axhspan(59.4, 60.6, color="tab:green", alpha=0.10,
               label="59.4-60.6 Hz band")
    ax.axhline(60.0, color="0.25", linestyle=":", linewidth=1.0,
               label="60 Hz nominal")
    ax.axhline(57.8, color="tab:red", linestyle="--", linewidth=1.0,
               label="57.8 Hz trip")
    ax.set_ylabel("Frequency (Hz)")
    ax.grid(alpha=0.3)


def setup_power_axis(ax):
    ax.set_ylabel("Power (MW)")
    ax.grid(alpha=0.3)


def plot_slice(n_points, max_points=200_000):
    step = max(1, int(np.ceil(n_points / max_points)))
    return slice(None, None, step)


def strongest_torsion_window(t_s, residual_kNm, window_s=10.0):
    dt = float(np.median(np.diff(t_s)))
    n_window = max(3, int(round(window_s / dt)))
    if residual_kNm.size <= n_window:
        rms = float(np.sqrt(np.mean(residual_kNm ** 2)))
        return float(t_s[0]), float(t_s[-1]), rms
    kernel = np.ones(n_window) / n_window
    rolling_mean_square = np.convolve(residual_kNm ** 2, kernel, mode="valid")
    start = int(np.argmax(rolling_mean_square))
    stop = min(start + n_window - 1, t_s.size - 1)
    rms = float(np.sqrt(rolling_mean_square[start]))
    return float(t_s[start]), float(t_s[stop]), rms


def plot_worst_segment(tseg, pseg, segment_results, outdir=DEFAULT_OUTDIR, dpi=160):
    fig, axes = plt.subplots(len(segment_results), 2, figsize=(12.0, 8.4), sharex=True)
    if len(segment_results) == 1:
        axes = np.array([axes])

    for row, (N, r) in enumerate(segment_results.items()):
        t_min = r.t_s / 60.0
        demand = np.interp(r.t_s, tseg, pseg)

        ax_power, ax_freq = axes[row]
        ax_power.step(t_min, demand, where="post", color="0.15",
                      linestyle="--", linewidth=1.0, label="Demand")
        ax_power.plot(t_min, r.Pe_mw, color="tab:blue", linewidth=1.0,
                      label="Delivered Pe")
        setup_power_axis(ax_power)
        ax_power.set_title(f"N={N}: demand vs delivered electrical power")
        ax_power.legend(fontsize=8, loc="upper right")

        ax_freq.plot(t_min, r.freq_hz, color="tab:orange", linewidth=1.0,
                     label="Frequency")
        setup_frequency_axis(ax_freq)
        ax_freq.set_title(f"N={N}: frequency response")
        ax_freq.legend(fontsize=8, loc="upper right")

    axes[-1, 0].set_xlabel("Time in worst 15-min segment (min)")
    axes[-1, 1].set_xlabel("Time in worst 15-min segment (min)")
    fig.suptitle("Fleet response to the MIT SuperCloud worst transient segment", fontsize=13)
    return finish_plot(fig, outdir / "fleet_study_n234_worst_segment.png", dpi)


def plot_full_n4(r4, outdir=DEFAULT_OUTDIR, dpi=160):
    t_min = r4.t_s / 60.0
    fig, axes = plt.subplots(2, 1, figsize=(12.0, 6.8), sharex=True)

    axes[0].step(t_min, r4.Pe_demand_mw, where="post", color="0.15",
                 linestyle="--", linewidth=0.9, label="Demand")
    axes[0].plot(t_min, r4.Pe_mw, color="tab:blue", linewidth=0.9,
                 label="Delivered Pe")
    setup_power_axis(axes[0])
    axes[0].set_title("N=4 full 2-hour window: demand and delivered electrical power")
    axes[0].legend(fontsize=8, loc="upper right")

    axes[1].plot(t_min, r4.freq_hz, color="tab:orange", linewidth=0.9,
                 label="Frequency")
    setup_frequency_axis(axes[1])
    axes[1].set_xlabel("Time in full window (min)")
    axes[1].set_title("N=4 full 2-hour window: frequency stays clear of trip")
    axes[1].legend(fontsize=8, loc="upper right")

    fig.suptitle("N=4 fleet ride-through on the MIT SuperCloud trace", fontsize=13)
    return finish_plot(fig, outdir / "fleet_study_n4_full_window.png", dpi)


def plot_torque_trace(N, torsion, outdir=DEFAULT_OUTDIR, dpi=160,
                      suffix="worst_segment", title_context="worst 15-min segment",
                      tripped=False):
    t_s = torsion["t_s"]
    t_min = t_s / 60.0
    T = torsion["T_shaft_kNm"]
    trend = torsion["T_trend_kNm"]
    residual = torsion["T_residual_kNm"]
    win_lo, win_hi, rms = strongest_torsion_window(t_s, residual)
    view = plot_slice(t_s.size)

    fig, axes = plt.subplots(2, 1, figsize=(12.0, 6.8), sharex=True)
    for ax in axes:
        ax.axvspan(win_lo / 60.0, win_hi / 60.0, color="tab:red", alpha=0.16,
                   label="highest torsional response window")
        ax.grid(alpha=0.3)

    axes[0].plot(t_min[view], T[view], color="tab:blue", linewidth=0.8,
                 label="PT-gen shaft torque")
    axes[0].plot(t_min[view], trend[view], color="0.15", linestyle="--",
                 linewidth=0.8, label="5 s trend")
    axes[0].set_ylabel("Torque (kNm)")
    axes[0].set_title(f"N={N}: per-shaft PT-gen torque ({title_context})")
    axes[0].legend(fontsize=8, loc="upper right")

    axes[1].plot(t_min[view], residual[view], color="tab:orange", linewidth=0.8,
                 label="detrended torsional torque")
    axes[1].axhline(0.0, color="0.25", linestyle=":", linewidth=1.0)
    axes[1].set_xlabel(f"Time in {title_context} (min)")
    axes[1].set_ylabel("Detrended torque (kNm)")
    axes[1].set_title(f"Highlighted 10 s residual RMS = {rms:.3f} kNm")
    axes[1].legend(fontsize=8, loc="upper right")

    note = (f"Torque range {T.min():.1f}..{T.max():.1f} kNm")
    if tripped:
        note += "\nPost-trip torque is not physical"
    axes[0].text(0.01, 0.04, note, transform=axes[0].transAxes,
                 fontsize=8, va="bottom", ha="left",
                 bbox={"facecolor": "white", "edgecolor": "0.65", "alpha": 0.92})

    fig.suptitle(f"Fleet study shaft torque: N={N}", fontsize=13)
    out_name = f"fleet_study_n{N}_torque_{suffix}.png"
    return finish_plot(fig, outdir / out_name, dpi)


def plot_segment_torque_traces(segment_torsion, segment_trips, outdir=DEFAULT_OUTDIR, dpi=160):
    outputs = []
    for N, torsion in segment_torsion.items():
        outputs.append(plot_torque_trace(
            N,
            torsion,
            outdir,
            dpi,
            suffix="worst_segment",
            title_context="worst 15-min segment",
            tripped=segment_trips.get(N, False),
        ))
    return outputs


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR,
                        help="Directory for generated figures. Default: docs/figs")
    parser.add_argument("--dpi", type=int, default=160,
                        help="PNG output resolution. Default: 160")
    parser.add_argument("--no-plots", action="store_true",
                        help="Run the text study without writing PNG figures.")
    return parser.parse_args()


def main():
    args = parse_args()
    t, p_mw = build_trace()
    print(f"trace: {p_mw.min():.1f}-{p_mw.max():.1f} MW, "
          f"worst step {np.diff(p_mw).min():.1f}/+{np.diff(p_mw).max():.1f} MW\n")

    # ---- segment (15 min around worst step): N=2, 3, 4 screening ----
    i_ev = int(np.argmax(np.abs(np.diff(p_mw))))
    lo = max(0, i_ev - 4500)
    hi = min(t.size, lo + 9000)
    tseg, pseg = t[lo:hi] - t[lo], p_mw[lo:hi]
    print("== 15-min worst segment (per-shaft fatigue, surviving N only) ==")
    seg = {}
    segment_results = {}
    segment_torsion = {}
    segment_trips = {}
    for N in [2, 3, 4]:
        r = run_fleet(tseg, pseg, N)
        segment_results[N] = r
        trip_s = float((r.freq_hz < 57.8).sum() * 0.02)
        segment_trips[N] = trip_s > 0.0
        if trip_s > 0.0:
            report(f"N={N} (segment)", r.freq_hz, 0.02)
            if not args.no_plots:
                segment_torsion[N] = per_shaft_torsion(r, N)
            print("  fatigue skipped: trajectory crosses 57.8 Hz; "
                  "post-trip life accounting is not physical")
            continue
        fat = per_shaft_torsion(r, N)
        segment_torsion[N] = fat
        seg[N] = fat
        report(f"N={N} (segment)", r.freq_hz, 0.02, fat)
    if 3 in seg and 4 in seg:
        for k in ["D_hcf", "D_lcf"]:
            m = 9.0 if k == "D_hcf" else 4.0
            ratio = seg[3][k] / max(seg[4][k], 1e-300)
            print(f"  {k} damage reduction N=3 -> N=4: {ratio:9.1f}x  "
                  f"(Basquin prediction from N4/N3=(4/3): {(4/3)**m:.1f}x)")

    # ---- full 2 h, N=4 (the unassisted-compliant fleet) ----
    print("\n== full 2 h window ==")
    r4 = run_fleet(t, p_mw, 4)
    fat4 = per_shaft_torsion(r4, 4)
    report("N=4 (full 2 h)", r4.freq_hz, 0.02, fat4)
    if not args.no_plots:
        outputs = [
            plot_worst_segment(tseg, pseg, segment_results, args.outdir, args.dpi),
            plot_full_n4(r4, args.outdir, args.dpi),
        ]
        outputs.extend(plot_segment_torque_traces(
            segment_torsion, segment_trips, args.outdir, args.dpi))
        outputs.append(plot_torque_trace(
            4,
            fat4,
            args.outdir,
            args.dpi,
            suffix="full_window",
            title_context="full 2 h window",
        ))
        print("\nGenerated fleet study figures:")
        for path in outputs:
            print(f"  {path.relative_to(ROOT)}")
    print("\nCompare: N=1 + BESS (notebook §6c): per-shaft D_window = 5.99e-10,"
          "\n         T 16.1..33.5 kNm — same negligible-fatigue class.")


if __name__ == "__main__":
    main()

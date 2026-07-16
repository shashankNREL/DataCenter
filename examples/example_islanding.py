"""Example 1: islanding survival + a naive-resync demonstration.

Setup:
    Bus(Gas) --- L_LOCAL --- Bus(DataCenter) --- L_TIE --- Bus(Grid)
        |                          |                          |
     235 MW GT                  200 MW load             infinite bus

Two scenarios are simulated and plotted side-by-side:

SCENARIO A — ISLANDING SURVIVAL (the headline result)
    t=0-2s    Grid-tied steady state.  Gas at ~85% load (≈200 MW), matching
              the data-center load.  Tie line carries near-zero flow.
    t=2s      Open L_TIE.  Gas alone must hold the local frequency.
    t=2-20s   Gas holds the load through droop control.  Frequency stays
              inside a tight band around 60 Hz.  This is the "data center
              survives loss of grid" demo.

SCENARIO B — NAIVE RESYNC
    Same setup, but L_TIE re-closes at t=8s without any phase-match check.
    Result: a large transient swing.  This is the *educational* part of the
    demo — it shows why real systems need a synchroscope plus an isochronous
    (zero-steady-state-error) governor mode before reclosing.  TGOV1's
    droop-only control gives a small steady-state frequency error during
    the islanded period; over a few seconds of islanding that integrates
    into a finite phase angle difference at the breaker terminals.  Closing
    into that mismatch drives the surge seen in panel B.

What this would take to "do right" (left for a later phase):
  - Switch governor to isochronous mode during island (integrate freq error
    so omega -> 1.0 exactly before resync), then back to droop after.
  - Add a synchroscope: only close when |delta_omega| < eps and |delta_theta|
    < ~5 degrees.
  Both require either custom ANDES models or driving Pref dynamically via
  the Alter event.

Run:
    ~/miniconda3/envs/datacenter/bin/python examples/example_islanding.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gas_plant import GasTurbinePlant
from gas_plant_andes import IslandedCaseConfig, run_islanding_scenario


def plot_scenario(axes_col, result, title, cfg, show_resync_band=False):
    """Render one column (4 panels) of the comparison figure."""
    ax_f, ax_p, ax_l, ax_e = axes_col

    ax_f.plot(result.t, result.freq_gas_hz, label="Gas rotor (ω)", lw=1.5)
    ax_f.plot(result.t, result.freq_dc_hz, label="DC bus measured", lw=1, ls="--", alpha=0.7)
    ax_f.axhline(60.0, color="gray", ls=":", lw=1, label="Nominal 60 Hz")
    if cfg.island_time_s is not None:
        end = cfg.resync_time_s if cfg.resync_time_s else result.t[-1]
        ax_f.axvspan(cfg.island_time_s, end, color="orange", alpha=0.10, label="Islanded")
    ax_f.set_title(title)
    ax_f.set_ylabel("Frequency [Hz]")
    ax_f.legend(loc="best", fontsize=8)
    ax_f.grid(True, alpha=0.3)

    ax_p.plot(result.t, result.plant_power_mw, color="C2", lw=1.5)
    ax_p.axhline(cfg.data_center_mw, color="C3", ls="--", lw=1, label=f"DC load ({cfg.data_center_mw:.0f} MW)")
    if cfg.island_time_s is not None:
        end = cfg.resync_time_s if cfg.resync_time_s else result.t[-1]
        ax_p.axvspan(cfg.island_time_s, end, color="orange", alpha=0.10)
    ax_p.set_ylabel("Gas Pm [MW]")
    ax_p.legend(loc="best", fontsize=8)
    ax_p.grid(True, alpha=0.3)

    ax_l.plot(result.t, result.line_tie_flow_mw, color="C4", lw=1.5)
    ax_l.axhline(0, color="gray", ls=":", lw=1)
    if cfg.island_time_s is not None:
        end = cfg.resync_time_s if cfg.resync_time_s else result.t[-1]
        ax_l.axvspan(cfg.island_time_s, end, color="orange", alpha=0.10)
    ax_l.set_ylabel("L_TIE flow [MW]\n(+ = grid→DC)")
    ax_l.grid(True, alpha=0.3)

    ax_e.plot(result.t, result.cumulative_fuel_kg, color="C5", lw=1.5, label="Fuel [kg]")
    ax_e2 = ax_e.twinx()
    ax_e2.plot(result.t, result.cumulative_co2_kg, color="C6", lw=1.5, ls="--", label="CO2 [kg]")
    ax_e.set_ylabel("Fuel [kg]", color="C5")
    ax_e.tick_params(axis="y", labelcolor="C5")
    ax_e2.set_ylabel("CO2 [kg]", color="C6")
    ax_e2.tick_params(axis="y", labelcolor="C6")
    ax_e.set_xlabel("Time [s]")
    if cfg.island_time_s is not None:
        end = cfg.resync_time_s if cfg.resync_time_s else result.t[-1]
        ax_e.axvspan(cfg.island_time_s, end, color="orange", alpha=0.10)
    ax_e.grid(True, alpha=0.3)
    h1, l1 = ax_e.get_legend_handles_labels()
    h2, l2 = ax_e2.get_legend_handles_labels()
    ax_e.legend(h1 + h2, l1 + l2, loc="best", fontsize=8)


def main():
    plant = GasTurbinePlant()  # 235 MW default, ThermoPower-derived surrogate

    print("=" * 60)
    print("Scenario A: islanding survival (no resync)")
    print("=" * 60)
    cfg_a = IslandedCaseConfig(
        plant=plant,
        plant_load_setpoint=0.85106,   # 235 MVA × 0.85106 = ~200 MW = DC load
        data_center_mw=200.0,
        data_center_mvar=0.0,
        island_time_s=2.0,
        resync_time_s=None,            # never reclose — pure island demo
    )
    print(f"Pre-island P_gas = {cfg_a.plant_p_mw:.2f} MW, P_load = {cfg_a.data_center_mw} MW")
    result_a = run_islanding_scenario(cfg_a, duration_s=20.0, tstep_s=0.005)
    sa = result_a.summary()
    print("Summary A:")
    for k, v in sa.items():
        print(f"  {k:25s} = {v:.4f}")

    print()
    print("=" * 60)
    print("Scenario B: islanding + naive resync (educational counterexample)")
    print("=" * 60)
    cfg_b = IslandedCaseConfig(
        plant=plant,
        plant_load_setpoint=0.85106,
        data_center_mw=200.0,
        data_center_mvar=0.0,
        island_time_s=2.0,
        resync_time_s=8.0,             # naive resync after 6 s of islanding
    )
    result_b = run_islanding_scenario(cfg_b, duration_s=20.0, tstep_s=0.005)
    sb = result_b.summary()
    print("Summary B:")
    for k, v in sb.items():
        print(f"  {k:25s} = {v:.4f}")

    # ---- side-by-side plot
    fig, axes = plt.subplots(4, 2, figsize=(16, 12), sharex=True)
    plot_scenario(axes[:, 0], result_a, "A — Islanding survival (no resync)", cfg_a)
    plot_scenario(axes[:, 1], result_b, "B — Naive resync (no phase matching)", cfg_b)

    fig.suptitle(
        "Gas plant + data-center load — islanding behaviour\n"
        "A: stable under droop control. B: see comments about synchroscope requirement.",
        fontsize=12,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))

    out_path = ROOT / "examples" / "output" / "islanding_resync.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=110)
    print(f"\nPlot written to {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

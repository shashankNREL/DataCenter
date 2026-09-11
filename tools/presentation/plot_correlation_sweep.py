from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from common import (
    DATA_DIR,
    bess_metrics,
    build_supercloud_trace,
    ensure_output_dirs,
    make_decorrelated_trace,
    save_figure,
    set_plot_style,
)


RHO_VALUES = np.array([0.0, 0.25, 0.50, 0.75, 1.0])
TAU_S = 10.0


def main() -> None:
    ensure_output_dirs()
    set_plot_style()

    trace = build_supercloud_trace()
    t_sec = trace["t_sec"]
    lockstep = trace["p_mw"]
    decorrelated = make_decorrelated_trace(lockstep, copies=64, seed=11)
    dt = float(np.median(np.diff(t_sec)))

    rows = []
    traces = {}
    for rho in RHO_VALUES:
        p_mw = float(rho) * lockstep + (1.0 - float(rho)) * decorrelated
        p_mw = np.maximum(p_mw, 0.0)
        traces[float(rho)] = p_mw
        dpdt = np.diff(p_mw) / dt
        bm = bess_metrics(p_mw, t_sec, TAU_S)
        rows.append({
            "coherence_factor": float(rho),
            "peak_mw": float(p_mw.max()),
            "mean_mw": float(p_mw.mean()),
            "max_abs_step_mw_per_100ms": float(np.abs(np.diff(p_mw)).max()),
            "p99_abs_dpdt_mw_s": float(np.percentile(np.abs(dpdt), 99)),
            "bess_power_mw_tau10": bm["bess_power_mw"],
            "bess_energy_kwh_tau10": 1000.0 * bm["bess_energy_mwh"],
            "equivalent_duration_s_tau10": bm["equivalent_duration_s"],
            "gt_max_ramp_mw_s_tau10": bm["gt_max_ramp_mw_s"],
        })

    metrics = pd.DataFrame(rows)
    metrics.to_csv(DATA_DIR / "correlation_sweep_metrics.csv", index=False)

    view = t_sec <= 8.0 * 60.0
    fig, axes = plt.subplots(2, 2, figsize=(12.5, 8.2))

    for rho, color in [(0.0, "#1B998B"), (0.5, "#F4A261"), (1.0, "#D1495B")]:
        axes[0, 0].plot(t_sec[view] / 60.0, traces[rho][view], lw=1.3,
                        color=color, label=f"coherence {rho:.2f}")
    axes[0, 0].set_title("Same average load, different synchronization")
    axes[0, 0].set_xlabel("Time (min)")
    axes[0, 0].set_ylabel("Facility load (MW)")
    axes[0, 0].legend(loc="upper right")

    axes[0, 1].plot(metrics["coherence_factor"], metrics["p99_abs_dpdt_mw_s"],
                    "o-", color="#2451A6")
    axes[0, 1].set_title("Fast ramp stress grows with synchronized jobs")
    axes[0, 1].set_xlabel("Coherence factor: 0 = decorrelated, 1 = lockstep")
    axes[0, 1].set_ylabel("p99 |dP/dt| (MW/s)")

    axes[1, 0].plot(metrics["coherence_factor"], metrics["bess_power_mw_tau10"],
                    "o-", color="#D1495B")
    axes[1, 0].set_title(f"BESS power for a {TAU_S:.0f} s split")
    axes[1, 0].set_xlabel("Coherence factor")
    axes[1, 0].set_ylabel("Battery power (MW)")

    axes[1, 1].plot(metrics["coherence_factor"], metrics["bess_energy_kwh_tau10"],
                    "o-", color="#F4A261", label="Energy")
    ax2 = axes[1, 1].twinx()
    ax2.plot(metrics["coherence_factor"], metrics["equivalent_duration_s_tau10"],
             "s--", color="#1B998B", label="Equivalent seconds")
    axes[1, 1].set_title("Energy stays small compared with power")
    axes[1, 1].set_xlabel("Coherence factor")
    axes[1, 1].set_ylabel("Energy swing (kWh)")
    ax2.set_ylabel("Equivalent duration at rated MW (s)")

    out = save_figure(fig, "04_correlation_sweep.png")
    plt.close(fig)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
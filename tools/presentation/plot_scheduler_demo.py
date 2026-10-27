from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.presentation.common import (  # noqa: E402
    DATA_DIR,
    ensure_output_dirs,
    save_figure,
    set_plot_style,
)
from tools.workload.scheduler_ramp_demo import (  # noqa: E402
    add_buffer_columns,
    compute_ramp_metrics,
    default_strategies,
    demo_workload,
    load_frontier_power_calibration,
    simulate_schedule,
)


def main() -> None:
    ensure_output_dirs()
    set_plot_style()

    calibration = load_frontier_power_calibration()
    jobs, base_active_nodes = demo_workload()
    strategies = {
        strategy.name: strategy for strategy in default_strategies()
        if strategy.name in {"immediate", "ramp-aware-0p5"}
    }
    schedules = {
        name: simulate_schedule(jobs, base_active_nodes, calibration, strategy)
        for name, strategy in strategies.items()
    }

    rows = []
    for name, schedule in schedules.items():
        ramp = compute_ramp_metrics(schedule.timeseries)
        buffer = add_buffer_columns(schedule, buffer_tau_s=10.0)
        rows.append({
            "strategy": name,
            "strategy_label": schedule.strategy.label,
            "scheduled_node_cohorts": int(schedule.timeseries["activated_nodes"].sum()),
            "max_abs_ramp_mw_s": ramp["max_abs_ramp_mw_s"],
            "bess_power_rating_mw": buffer["bess_power_rating_mw"],
            "p95_full_start_delay_s": float(
                schedule.jobs["full_start_delay_s"].quantile(0.95)
            ),
            "makespan_s": int(schedule.jobs["completion_s"].max()),
        })
    summary = pd.DataFrame(rows).set_index("strategy")
    summary.to_csv(DATA_DIR / "scheduler_demo_summary.csv")

    immediate = schedules["immediate"].timeseries
    controlled = schedules["ramp-aware-0p5"].timeseries
    immediate_metrics = summary.loc["immediate"]
    controlled_metrics = summary.loc["ramp-aware-0p5"]

    fig = plt.figure(figsize=(12.5, 7.2))
    grid = fig.add_gridspec(
        2,
        height_ratios=[4.5, 1.25],
        hspace=0.28,
    )
    ax_trace = fig.add_subplot(grid[0])
    ax_metrics = fig.add_subplot(grid[1])

    window = (immediate["time_s"] >= 116) & (immediate["time_s"] <= 136)
    controlled_window = (
        (controlled["time_s"] >= 116) & (controlled["time_s"] <= 136)
    )
    ax_trace.step(
        immediate.loc[window, "time_s"] - 120,
        immediate.loc[window, "facility_power_mw"],
        where="post",
        color="#D1495B",
        linewidth=3.0,
        label="Ordinary scheduler: start everything now",
    )
    ax_trace.step(
        controlled.loc[controlled_window, "time_s"] - 120,
        controlled.loc[controlled_window, "facility_power_mw"],
        where="post",
        color="#1B998B",
        linewidth=3.0,
        label="Power-aware scheduler: start small groups",
    )
    ax_trace.axvline(0.0, color="#555555", linestyle=":", linewidth=1.2)
    ax_trace.text(0.25, 11.2, "Jobs arrive", fontsize=11, color="#555555")
    ax_trace.annotate(
        "One large power shock",
        xy=(0.5, 16.95),
        xytext=(4.2, 15.4),
        arrowprops={"arrowstyle": "->", "color": "#D1495B"},
        color="#D1495B",
        fontsize=12,
        fontweight="bold",
    )
    ax_trace.annotate(
        "Same destination, gentler climb",
        xy=(11.5, 16.8),
        xytext=(2.2, 13.4),
        arrowprops={"arrowstyle": "->", "color": "#1B998B"},
        color="#14796D",
        fontsize=12,
        fontweight="bold",
    )
    ax_trace.set_xlabel("Seconds after the first group of jobs arrives")
    ax_trace.set_ylabel("Facility power (MW)")
    ax_trace.set_title("The scheduler changes when GPUs turn on")
    ax_trace.set_xlim(-4, 16)
    ax_trace.set_ylim(10.5, 17.5)
    ax_trace.legend(loc="lower right", fontsize=9)

    ax_metrics.axis("off")
    ramp_reduction = 100.0 * (
        1.0 - controlled_metrics["max_abs_ramp_mw_s"]
        / immediate_metrics["max_abs_ramp_mw_s"]
    )
    buffer_reduction = 100.0 * (
        1.0 - controlled_metrics["bess_power_rating_mw"]
        / immediate_metrics["bess_power_rating_mw"]
    )
    metrics = [
        (
            f"{immediate_metrics['max_abs_ramp_mw_s']:.2f} -> "
            f"{controlled_metrics['max_abs_ramp_mw_s']:.2f} MW/s",
            f"{ramp_reduction:.0f}% smaller power shock",
        ),
        (
            f"{immediate_metrics['bess_power_rating_mw']:.2f} -> "
            f"{controlled_metrics['bess_power_rating_mw']:.2f} MW",
            f"{buffer_reduction:.0f}% less fast-buffer power",
        ),
        (
            f"{controlled_metrics['p95_full_start_delay_s']:.1f} seconds",
            "p95 added full-start delay",
        ),
    ]
    for index, (value, meaning) in enumerate(metrics):
        x = (index + 0.5) / len(metrics)
        if index:
            ax_metrics.axvline(index / len(metrics), color="#CCCCCC", linewidth=1.0)
        ax_metrics.text(x, 0.66, value, ha="center", va="center", fontsize=14, fontweight="bold")
        ax_metrics.text(x, 0.28, meaning, ha="center", va="center", fontsize=10, color="#555555")

    fig.suptitle(
        "A few seconds of scheduling flexibility can soften the electrical shock",
        fontsize=19,
        y=0.99,
    )
    out = save_figure(fig, "09_scheduler_demo.png")
    plt.close(fig)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
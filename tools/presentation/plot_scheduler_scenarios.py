from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.presentation.common import (  # noqa: E402
    DATA_DIR,
    FIGURE_DIR,
    ensure_output_dirs,
    set_plot_style,
)
from tools.workload.scheduler_ramp_demo import (  # noqa: E402
    JobSpec,
    Strategy,
    load_frontier_power_calibration,
    simulate_schedule,
)


@dataclass(frozen=True)
class Scenario:
    name: str
    background_nodes: int
    durations_s: tuple[int, ...]
    immediate_ramp_mw_s: float
    peak_active_nodes: int
    max_capacity_wait_s: int
    max_extra_delay_s: int
    completion_extension_s: int
    best_battery_peak_mw: float
    best_frequency_nadir_hz: float
    best_operating_cost: float
    interpretation: str


NODE_COUNTS = (1200, 900, 700, 1100, 900, 700, 1000, 800, 600)
SUBMIT_TIMES = (120, 120, 120, 360, 360, 360, 600, 600, 600)
SYNCHRONIZED_DURATIONS = (240, 240, 240, 240, 240, 240, 180, 180, 180)
IRREGULAR_DURATIONS = (190, 270, 310, 175, 265, 205, 180, 230, 150)
WAVE_COLORS = ("#2451A6",) * 3 + ("#1B998B",) * 3 + ("#E07A3F",) * 3

SCENARIOS = (
    Scenario(
        "SYNCHRONIZED",
        1600,
        SYNCHRONIZED_DURATIONS,
        6.184524,
        4400,
        0,
        12,
        10,
        0.0,
        59.472354,
        316.024051,
        "Aligned endings make replacement work easy to pace.",
    ),
    Scenario(
        "IRREGULAR",
        1600,
        IRREGULAR_DURATIONS,
        6.184524,
        5900,
        0,
        12,
        7,
        2.215966,
        59.406568,
        330.095633,
        "Scattered endings create repeated disturbances; storage still helps.",
    ),
    Scenario(
        "BUSY",
        6800,
        SYNCHRONIZED_DURATIONS,
        5.901803,
        9472,
        240,
        11,
        0,
        0.0,
        59.482966,
        431.794069,
        "The cluster is full, so some waiting is unavoidable even without smoothing.",
    ),
)


def build_jobs(durations_s: tuple[int, ...]) -> list[JobSpec]:
    return [
        JobSpec(
            job_id=f"batch-{chr(ord('a') + index)}",
            submit_s=SUBMIT_TIMES[index],
            duration_s=durations_s[index],
            nodes=NODE_COUNTS[index],
        )
        for index in range(len(NODE_COUNTS))
    ]


def build_scenario_tables() -> tuple[pd.DataFrame, pd.DataFrame]:
    calibration = load_frontier_power_calibration()
    immediate = Strategy("immediate", "Immediate", "immediate")
    optimized = Strategy(
        "wave-226",
        "Best tested within 15 s: 226 nodes/s",
        "fixed",
        activation_limit_nodes=226,
    )
    timeline_rows = []
    result_rows = []

    for scenario in SCENARIOS:
        jobs = build_jobs(scenario.durations_s)
        immediate_run = simulate_schedule(
            jobs, scenario.background_nodes, calibration, immediate
        )
        optimized_run = simulate_schedule(
            jobs, scenario.background_nodes, calibration, optimized
        )
        immediate_jobs = immediate_run.jobs.set_index("job_id")

        for _, job in optimized_run.jobs.iterrows():
            immediate_job = immediate_jobs.loc[job["job_id"]]
            timeline_rows.append({
                "scenario": scenario.name.lower(),
                "job_id": job["job_id"],
                "submit_s": int(job["submit_s"]),
                "nodes": int(job["nodes"]),
                "duration_s": int(job["duration_s"]),
                "immediate_full_start_s": int(immediate_job["full_start_s"]),
                "optimized_first_start_s": int(job["first_start_s"]),
                "optimized_full_start_s": int(job["full_start_s"]),
                "optimized_completion_s": int(job["completion_s"]),
                "capacity_wait_s": int(
                    immediate_job["full_start_s"] - job["submit_s"]
                ),
                "extra_smoothing_delay_s": int(
                    job["full_start_s"] - immediate_job["full_start_s"]
                ),
            })

        result_rows.append({
            "scenario": scenario.name.lower(),
            "selected_policy": "226 nodes/s",
            "background_nodes": scenario.background_nodes,
            "peak_active_nodes": scenario.peak_active_nodes,
            "immediate_max_ramp_mw_s": scenario.immediate_ramp_mw_s,
            "optimized_max_ramp_mw_s": 0.499179,
            "max_capacity_wait_s": scenario.max_capacity_wait_s,
            "max_extra_delay_s": scenario.max_extra_delay_s,
            "completion_extension_s": scenario.completion_extension_s,
            "best_battery_peak_mw": scenario.best_battery_peak_mw,
            "best_frequency_nadir_hz": scenario.best_frequency_nadir_hz,
            "best_operating_cost": scenario.best_operating_cost,
        })

    return pd.DataFrame(timeline_rows), pd.DataFrame(result_rows)


def draw_job_rulers(ax, timeline: pd.DataFrame, scenario: Scenario) -> None:
    for index, (_, job) in enumerate(timeline.iterrows()):
        y = len(timeline) - 1 - index
        color = WAVE_COLORS[index]
        submit_s = float(job["submit_s"])
        duration_s = float(job["duration_s"])
        first_start_s = float(job["optimized_first_start_s"])
        full_start_s = float(job["optimized_full_start_s"])
        completion_s = float(job["optimized_completion_s"])
        first_finish_s = min(first_start_s + duration_s, completion_s)

        if full_start_s > first_start_s:
            ax.barh(
                y,
                full_start_s - first_start_s,
                left=first_start_s,
                height=0.56,
                color=color,
                alpha=0.28,
                zorder=2,
            )
        if first_finish_s > full_start_s:
            ax.barh(
                y,
                first_finish_s - full_start_s,
                left=full_start_s,
                height=0.56,
                color=color,
                alpha=0.90,
                zorder=3,
            )
        if completion_s > first_finish_s:
            ax.barh(
                y,
                completion_s - first_finish_s,
                left=first_finish_s,
                height=0.56,
                color=color,
                alpha=0.28,
                zorder=2,
            )

        ax.plot(submit_s, y, marker="v", color="#333333", markersize=4, zorder=5)
        immediate_start_s = float(job["immediate_full_start_s"])
        if immediate_start_s > submit_s:
            ax.plot(
                [submit_s, immediate_start_s],
                [y + 0.38, y + 0.38],
                color="#6C757D",
                linewidth=2.2,
                solid_capstyle="butt",
                zorder=4,
            )

    for arrival_s in sorted(set(SUBMIT_TIMES)):
        ax.axvline(arrival_s, color="#BBBBBB", linestyle=":", linewidth=0.9)

    labels = [
        f"{chr(ord('A') + index)}  {nodes:,}"
        for index, nodes in reversed(list(enumerate(NODE_COUNTS)))
    ]
    ax.set_yticks(range(len(timeline)), labels=labels)
    ax.set_ylim(-0.7, len(timeline) - 0.3)
    ax.set_xlim(100, 860)
    ax.set_ylabel("Job   nodes")
    ax.grid(axis="x", alpha=0.22)
    ax.grid(axis="y", visible=False)
    available = 9472 - scenario.background_nodes
    ax.set_title(
        f"{scenario.name.title()}  |  {scenario.background_nodes:,} always on  |  "
        f"{available:,} available for jobs",
        loc="left",
        fontweight="bold",
        fontsize=12,
    )


def draw_result_card(ax, scenario: Scenario) -> None:
    ax.axis("off")
    reduction = 100.0 * (1.0 - 0.499179 / scenario.immediate_ramp_mw_s)
    battery = f"{scenario.best_battery_peak_mw:.2f} MW"
    finish = (
        "No later finish"
        if scenario.completion_extension_s == 0
        else f"Finish +{scenario.completion_extension_s} s"
    )
    ax.text(0.02, 0.93, "BEST TESTED", color="#2451A6", fontsize=11,
            fontweight="bold", transform=ax.transAxes)
    ax.text(0.02, 0.80, "226 nodes/s", fontsize=13, fontweight="bold",
            transform=ax.transAxes)
    ax.text(0.02, 0.61, f"Ramp   {scenario.immediate_ramp_mw_s:.2f} -> 0.50 MW/s",
            fontsize=10.5, transform=ax.transAxes)
    ax.text(0.02, 0.48, f"Battery   {battery}", fontsize=10.5,
            transform=ax.transAxes)
    ax.text(0.02, 0.35, f"Minimum   {scenario.best_frequency_nadir_hz:.3f} Hz",
            fontsize=10.5, transform=ax.transAxes)
    ax.text(0.02, 0.22, finish,
            fontsize=9.5, transform=ax.transAxes)
    ax.text(0.02, 0.09, f"{reduction:.0f}% ramp reduction",
            fontsize=9.5, color="#555555", transform=ax.transAxes)


def main() -> None:
    ensure_output_dirs()
    set_plot_style()
    timeline, results = build_scenario_tables()
    timeline.to_csv(DATA_DIR / "scheduler_scenario_timeline.csv", index=False)
    results.to_csv(DATA_DIR / "scheduler_scenario_results.csv", index=False)

    fig = plt.figure(figsize=(15.5, 9.0))
    grid = fig.add_gridspec(
        3,
        2,
        width_ratios=[4.4, 1.75],
        hspace=0.40,
        wspace=0.08,
        left=0.09,
        right=0.98,
        top=0.82,
        bottom=0.09,
    )

    timeline_axes = []
    for row, scenario in enumerate(SCENARIOS):
        ax_timeline = fig.add_subplot(
            grid[row, 0], sharex=timeline_axes[0] if timeline_axes else None
        )
        timeline_axes.append(ax_timeline)
        scenario_timeline = timeline[timeline["scenario"] == scenario.name.lower()]
        draw_job_rulers(ax_timeline, scenario_timeline, scenario)
        draw_result_card(fig.add_subplot(grid[row, 1]), scenario)
        if row < len(SCENARIOS) - 1:
            ax_timeline.tick_params(axis="x", labelbottom=False)
        else:
            ax_timeline.set_xlabel("Simulation time (seconds)")

    legend = [
        Line2D([0], [0], marker="v", color="none", markerfacecolor="#333333",
               markeredgecolor="#333333", label="Job requested", markersize=7),
          Patch(facecolor="#2451A6", alpha=0.28,
              label="Some nodes active"),
        Patch(facecolor="#2451A6", alpha=0.90,
              label="All nodes active"),
        Line2D([0], [0], color="#6C757D", linewidth=3,
               label="Unavoidable capacity wait"),
    ]
    fig.legend(
        handles=legend,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.885),
        ncol=4,
        frameon=False,
        fontsize=9,
    )
    fig.suptitle(
        "Where the nodes go: three scheduler optimization scenarios",
        fontsize=19,
        y=0.985,
    )
    fig.text(
        0.09,
        0.94,
        "Each row is one job. Bars that overlap horizontally use nodes at the same time. "
        "Results use the best tested policy with no more than 15 seconds of added start delay.",
        fontsize=9.5,
        color="#555555",
    )

    out = FIGURE_DIR / "10_scheduler_scenario_rulers.png"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()

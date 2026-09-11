"""Demonstrate scheduler-controlled GPU activation for facility ramp reduction.

The prototype treats each scheduled cohort as an elastic group of four-GPU
Frontier nodes. Cohorts activated at different seconds finish at correspondingly
different seconds, preserving node-seconds of work while exposing the delay and
makespan cost of ramp control.

Run from the repository root:
    pixi run python tools/workload/scheduler_ramp_demo.py
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from gas_plant.dynamics.ggov1 import GGOV1Params  # noqa: E402
from gas_plant.dynamics.multishaft import (  # noqa: E402
    MultishaftParams,
    MultishaftResult,
    simulate_multishaft,
)
from gas_plant.dynamics.torsional import (  # noqa: E402
    TorsionalParams,
    compute_shaft_torques,
    detrend_rolling_median,
    miners_damage,
    rainflow_count,
)
from gas_plant.unit import GasTurbinePlant  # noqa: E402
from tools.presentation.common import bess_metrics, set_plot_style  # noqa: E402


DEFAULT_OUTDIR = ROOT / "Presentation" / "scheduler_ramp_demo"
LM2500_FULL_LOAD_EXHAUST_K = 791.0
FREQUENCY_BAND_HZ = (59.4, 60.6)
UNDERFREQUENCY_TRIP_HZ = 57.8


@dataclass(frozen=True)
class JobSpec:
    job_id: str
    submit_s: int
    duration_s: int
    nodes: int


@dataclass(frozen=True)
class Strategy:
    name: str
    label: str
    mode: str
    activation_limit_nodes: int | None = None
    ramp_limit_mw_s: float | None = None


@dataclass(frozen=True)
class PowerCalibration:
    available_nodes: int
    gpus_per_node: int
    facility_idle_mw: float
    active_node_delta_mw: float


@dataclass
class ScheduleRun:
    strategy: Strategy
    timeseries: pd.DataFrame
    jobs: pd.DataFrame


@dataclass
class TurbineRun:
    dynamics: MultishaftResult
    torsion: dict[str, object] | None


def load_frontier_power_calibration() -> PowerCalibration:
    config_dir = ROOT / "RAPS" / "config" / "frontier"
    with open(config_dir / "system.json", encoding="utf-8") as stream:
        system = json.load(stream)
    with open(config_dir / "power.json", encoding="utf-8") as stream:
        power = json.load(stream)

    physical_racks = system["NUM_CDUS"] * system["RACKS_PER_CDU"]
    available_racks = physical_racks - len(system["MISSING_RACKS"])
    available_nodes = available_racks * system["NODES_PER_RACK"]

    def node_power_after_sivoc(cpu_util: float, gpu_util: float) -> float:
        cpu_w = (cpu_util * power["POWER_CPU_MAX"]
                 + (system["CPUS_PER_NODE"] - cpu_util) * power["POWER_CPU_IDLE"])
        gpu_w = (gpu_util * power["POWER_GPU_MAX"]
                 + (system["GPUS_PER_NODE"] - gpu_util) * power["POWER_GPU_IDLE"])
        node_w = (cpu_w + gpu_w + power["POWER_MEM"]
                  + system["NICS_PER_NODE"] * power["POWER_NIC"]
                  + power["POWER_NVME"])
        return ((node_w + power["SIVOC_LOSS_CONSTANT"])
                / power["SIVOC_EFFICIENCY"])

    idle_sivoc_w = node_power_after_sivoc(0.0, 0.0)
    active_sivoc_w = node_power_after_sivoc(
        float(system["CPUS_PER_NODE"]), float(system["GPUS_PER_NODE"])
    )
    nodes_per_rectifier = system["NODES_PER_RECTIFIER"]
    idle_node_ac_w = (
        nodes_per_rectifier * idle_sivoc_w + power["RECTIFIER_LOSS_CONSTANT"]
    ) / power["RECTIFIER_EFFICIENCY"] / nodes_per_rectifier
    active_node_delta_w = (
        active_sivoc_w - idle_sivoc_w
    ) / power["RECTIFIER_EFFICIENCY"]

    switch_w = (available_racks * system["CHASSIS_PER_RACK"]
                * system["SWITCHES_PER_CHASSIS"] * power["POWER_SWITCH"])
    cdu_w = system["NUM_CDUS"] * power["POWER_CDU"]
    facility_idle_mw = (
        available_nodes * idle_node_ac_w
        + switch_w / power["RECTIFIER_EFFICIENCY"]
        + cdu_w
    ) / 1e6
    return PowerCalibration(
        available_nodes=available_nodes,
        gpus_per_node=system["GPUS_PER_NODE"],
        facility_idle_mw=facility_idle_mw,
        active_node_delta_mw=active_node_delta_w / 1e6,
    )


def demo_workload() -> tuple[list[JobSpec], int]:
    """Three launch waves with replacement work available at completions."""
    jobs = [
        JobSpec("train-a", 120, 240, 1200),
        JobSpec("train-b", 120, 240, 900),
        JobSpec("inference-a", 120, 240, 700),
        JobSpec("train-c", 360, 240, 1100),
        JobSpec("inference-b", 360, 240, 900),
        JobSpec("analysis-a", 360, 240, 700),
        JobSpec("train-d", 600, 180, 1000),
        JobSpec("inference-c", 600, 180, 800),
        JobSpec("analysis-b", 600, 180, 600),
    ]
    return jobs, 1600


def default_strategies() -> list[Strategy]:
    return [
        Strategy("immediate", "Immediate activation", "immediate"),
        Strategy("wave-512", "Fixed wave: 512 nodes/s", "fixed",
                 activation_limit_nodes=512),
        Strategy("wave-256", "Fixed wave: 256 nodes/s", "fixed",
                 activation_limit_nodes=256),
        Strategy("ramp-aware-0p5", "Completion-aware: 0.5 MW/s", "ramp",
                 ramp_limit_mw_s=0.5),
    ]


def _activation_allowance(
    strategy: Strategy,
    free_nodes: int,
    node_delta_mw: float,
) -> int:
    if strategy.mode == "immediate":
        return free_nodes
    if strategy.mode == "fixed":
        if strategy.activation_limit_nodes is None:
            raise ValueError("fixed strategy requires activation_limit_nodes")
        return min(free_nodes, strategy.activation_limit_nodes)
    if strategy.mode == "ramp":
        if strategy.ramp_limit_mw_s is None:
            raise ValueError("ramp strategy requires ramp_limit_mw_s")
        budget_nodes = max(1, int(np.floor(strategy.ramp_limit_mw_s / node_delta_mw)))
        return min(free_nodes, budget_nodes)
    raise ValueError(f"unknown strategy mode: {strategy.mode}")


def simulate_schedule(
    jobs: list[JobSpec],
    base_active_nodes: int,
    calibration: PowerCalibration,
    strategy: Strategy,
    settle_s: int = 120,
) -> ScheduleRun:
    jobs_by_id = {job.job_id: job for job in jobs}
    ordered_jobs = sorted(jobs, key=lambda job: (job.submit_s, job.job_id))
    remaining = {job.job_id: job.nodes for job in jobs}
    pending: list[str] = []
    cohorts: list[tuple[int, str, int]] = []
    first_start: dict[str, int] = {}
    full_start: dict[str, int] = {}
    completion: dict[str, int] = {}
    rows: list[dict[str, float | int]] = []
    submit_index = 0
    current_s = 0
    held_active_nodes = 0
    finished_at_s: int | None = None
    max_horizon_s = max(job.submit_s + job.duration_s for job in jobs) + 7200

    while current_s <= max_horizon_s:
        ending = [cohort for cohort in cohorts if cohort[0] <= current_s]
        cohorts = [cohort for cohort in cohorts if cohort[0] > current_s]
        completed_nodes = sum(cohort[2] for cohort in ending)
        for end_s, job_id, _ in ending:
            completion[job_id] = max(completion.get(job_id, 0), end_s)
        if strategy.mode == "ramp":
            held_active_nodes += completed_nodes

        while (submit_index < len(ordered_jobs)
               and ordered_jobs[submit_index].submit_s <= current_s):
            pending.append(ordered_jobs[submit_index].job_id)
            submit_index += 1

        activated_nodes = 0
        powered_up_nodes = 0
        idled_nodes = completed_nodes if strategy.mode != "ramp" else 0

        def activate_pending(allowance: int) -> int:
            activated_total = 0
            while pending and allowance > 0:
                job_id = pending[0]
                activated = min(remaining[job_id], allowance)
                job = jobs_by_id[job_id]
                cohorts.append((current_s + job.duration_s, job_id, activated))
                first_start.setdefault(job_id, current_s)
                remaining[job_id] -= activated
                allowance -= activated
                activated_total += activated
                if remaining[job_id] == 0:
                    full_start[job_id] = current_s
                    pending.pop(0)
            return activated_total

        if strategy.mode == "ramp":
            reused_nodes = activate_pending(held_active_nodes)
            held_active_nodes -= reused_nodes
            activated_nodes += reused_nodes

        active_elastic_nodes = sum(cohort[2] for cohort in cohorts)
        free_nodes = (calibration.available_nodes - base_active_nodes
                      - active_elastic_nodes - held_active_nodes)
        if strategy.mode == "ramp" and (completed_nodes > 0 or held_active_nodes > 0):
            allowance = 0
        else:
            allowance = _activation_allowance(
                strategy, free_nodes, calibration.active_node_delta_mw
            )
        powered_up_nodes = activate_pending(allowance)
        activated_nodes += powered_up_nodes

        if strategy.mode == "ramp" and not pending:
            idle_budget = max(
                1,
                int(np.floor(
                    strategy.ramp_limit_mw_s / calibration.active_node_delta_mw
                )),
            )
            idled_nodes = min(held_active_nodes, idle_budget)
            held_active_nodes -= idled_nodes

        active_nodes = (
            base_active_nodes + sum(cohort[2] for cohort in cohorts)
            + held_active_nodes
        )
        rows.append({
            "time_s": current_s,
            "active_nodes": active_nodes,
            "active_gpus": active_nodes * calibration.gpus_per_node,
            "activated_nodes": activated_nodes,
            "powered_up_nodes": powered_up_nodes,
            "completed_nodes": completed_nodes,
            "idled_nodes": idled_nodes,
            "held_active_nodes": held_active_nodes,
            "pending_nodes": sum(remaining[job_id] for job_id in pending),
        })

        finished = (
            submit_index == len(ordered_jobs) and not pending and not cohorts
            and held_active_nodes == 0
        )
        if finished:
            if finished_at_s is None:
                finished_at_s = current_s
            if current_s >= finished_at_s + settle_s:
                break
        current_s += 1
    else:
        raise RuntimeError(f"{strategy.name} did not finish by {max_horizon_s} s")

    frame = pd.DataFrame(rows)
    frame["facility_power_mw"] = (
        calibration.facility_idle_mw
        + frame["active_nodes"] * calibration.active_node_delta_mw
    )
    frame["ramp_mw_s"] = frame["facility_power_mw"].diff().fillna(0.0)

    job_rows = []
    for job in ordered_jobs:
        job_rows.append({
            "strategy": strategy.name,
            "job_id": job.job_id,
            "submit_s": job.submit_s,
            "nodes": job.nodes,
            "duration_s": job.duration_s,
            "first_start_s": first_start[job.job_id],
            "full_start_s": full_start[job.job_id],
            "completion_s": completion[job.job_id],
            "first_start_delay_s": first_start[job.job_id] - job.submit_s,
            "full_start_delay_s": full_start[job.job_id] - job.submit_s,
            "turnaround_s": completion[job.job_id] - job.submit_s,
        })
    return ScheduleRun(strategy, frame, pd.DataFrame(job_rows))


def compute_ramp_metrics(frame: pd.DataFrame) -> dict[str, float]:
    ramp = frame["ramp_mw_s"].to_numpy(dtype=float)[1:]
    ramp_events = np.abs(ramp[np.abs(ramp) > 1e-12])
    power = frame["facility_power_mw"].to_numpy(dtype=float)
    ten_second_ramp = (power[10:] - power[:-10]) / 10.0
    return {
        "max_up_ramp_mw_s": float(np.max(ramp)),
        "max_down_ramp_mw_s": float(np.min(ramp)),
        "max_abs_ramp_mw_s": float(np.max(np.abs(ramp))),
        "p95_abs_ramp_mw_s": float(np.percentile(np.abs(ramp), 95)),
        "p99_abs_ramp_mw_s": float(np.percentile(np.abs(ramp), 99)),
        "p99_event_abs_ramp_mw_s": float(np.percentile(ramp_events, 99)),
        "max_10s_abs_ramp_mw_s": float(np.max(np.abs(ten_second_ramp))),
        "seconds_above_1_mw_s": float(np.sum(np.abs(ramp) > 1.0)),
    }


def lm2500_dispatch_estimate(load_fraction: np.ndarray) -> dict[str, np.ndarray]:
    """Screening exhaust estimate: ThermoPower shape, LM2500 full-load anchor."""
    plant = GasTurbinePlant(rated_power_mw=22.0)
    result = plant.dispatch(load_fraction)
    raw_full_load_k = float(plant.dispatch(1.0)["exhaust_T_K"])
    result["exhaust_T_K"] = (
        np.asarray(result["exhaust_T_K"], dtype=float)
        + LM2500_FULL_LOAD_EXHAUST_K - raw_full_load_k
    )
    return {key: np.asarray(value) for key, value in result.items()}


def run_torsion(
    result: MultishaftResult,
    fleet_size: int,
    sample_rate_hz: float,
) -> dict[str, object]:
    per_shaft = copy.copy(result)
    per_shaft.Pm_pt_mw = result.Pm_pt_mw / fleet_size
    per_shaft.Pe_mw = result.Pe_mw / fleet_size
    params = TorsionalParams()
    shaft = compute_shaft_torques(
        per_shaft, params=params, sample_rate_hz=sample_rate_hz
    )
    torque = np.asarray(shaft["T_shaft_kNm"], dtype=float)
    time_s = np.asarray(shaft["t"], dtype=float)
    residual, trend = detrend_rolling_median(torque, time_s, window_s=5.0)
    hcf = miners_damage(rainflow_count(residual, time_s), params, use_goodman=True)
    lcf_params = TorsionalParams(m_fatigue=4.0, N_ref=1e4, Sa_ref_mpa=500.0)
    lcf = miners_damage(
        rainflow_count(trend, time_s), lcf_params, use_goodman=True
    )
    return {
        "time_s": time_s,
        "torque_kNm": torque,
        "torque_trend_kNm": trend,
        "torque_residual_kNm": residual,
        "D_hcf": float(hcf["d_i"].sum()),
        "D_lcf": float(lcf["d_i"].sum()),
    }


def run_turbine(
    schedule: ScheduleRun,
    fleet_size: int,
    sample_dt_s: float,
    torsion_sample_rate_hz: float,
) -> TurbineRun:
    time_s = schedule.timeseries["time_s"].to_numpy(dtype=float)
    load_mw = schedule.timeseries["facility_power_mw"].to_numpy(dtype=float)
    params = GGOV1Params.lm2500_overrides(
        Sn_mva=23.0 * fleet_size,
        Trate_mw=22.0 * fleet_size,
    )
    if load_mw.max() > params.Pm_thermal_max_pu * params.Trate_mw:
        raise ValueError(
            f"workload peak {load_mw.max():.2f} MW exceeds fleet continuous "
            f"rating {params.Pm_thermal_max_pu * params.Trate_mw:.2f} MW"
        )
    dynamics = simulate_multishaft(
        time_s,
        load_mw,
        params=MultishaftParams(ggov1=params),
        sample_dt_s=sample_dt_s,
        dispatch_fn=lm2500_dispatch_estimate,
    )
    trip_s = float(np.sum(dynamics.freq_hz < UNDERFREQUENCY_TRIP_HZ) * sample_dt_s)
    torsion = None
    if trip_s == 0.0:
        torsion = run_torsion(dynamics, fleet_size, torsion_sample_rate_hz)
    return TurbineRun(dynamics, torsion)


def compute_turbine_metrics(run: TurbineRun, sample_dt_s: float) -> dict[str, float]:
    result = run.dynamics
    low_hz, high_hz = FREQUENCY_BAND_HZ
    temperature_k = np.asarray(result.exhaust_T_K, dtype=float)
    temperature_rate = np.diff(temperature_k) / sample_dt_s
    temperature_rate_events = np.abs(
        temperature_rate[np.abs(temperature_rate) > 1e-9]
    )
    metrics = {
        "frequency_nadir_hz": float(np.min(result.freq_hz)),
        "frequency_zenith_hz": float(np.max(result.freq_hz)),
        "frequency_oob_s": float(
            np.sum((result.freq_hz < low_hz) | (result.freq_hz > high_hz))
            * sample_dt_s
        ),
        "frequency_below_trip_s": float(
            np.sum(result.freq_hz < UNDERFREQUENCY_TRIP_HZ) * sample_dt_s
        ),
        "exhaust_temp_min_c": float(np.min(temperature_k) - 273.15),
        "exhaust_temp_max_c": float(np.max(temperature_k) - 273.15),
        "exhaust_temp_range_k": float(np.ptp(temperature_k)),
        "max_abs_temp_rate_k_s": float(np.max(np.abs(temperature_rate))),
        "p99_abs_temp_rate_k_s": float(np.percentile(np.abs(temperature_rate), 99)),
        "p99_event_abs_temp_rate_k_s": float(
            np.percentile(temperature_rate_events, 99)
        ),
        "total_temp_variation_k": float(np.sum(np.abs(np.diff(temperature_k)))),
        "max_thermal_proxy_pu": float(np.max(result.thermal_load_proxy_pu)),
    }
    if run.torsion is None:
        metrics.update({
            "torque_min_kNm": np.nan,
            "torque_max_kNm": np.nan,
            "torque_range_kNm": np.nan,
            "torsional_damage_hcf": np.nan,
            "torsional_damage_lcf": np.nan,
        })
    else:
        torque = np.asarray(run.torsion["torque_kNm"], dtype=float)
        metrics.update({
            "torque_min_kNm": float(np.min(torque)),
            "torque_max_kNm": float(np.max(torque)),
            "torque_range_kNm": float(np.ptp(torque)),
            "torsional_damage_hcf": float(run.torsion["D_hcf"]),
            "torsional_damage_lcf": float(run.torsion["D_lcf"]),
        })
    return metrics


def add_buffer_columns(
    schedule: ScheduleRun,
    buffer_tau_s: float,
) -> dict[str, float]:
    frame = schedule.timeseries
    metrics = bess_metrics(
        frame["facility_power_mw"].to_numpy(dtype=float),
        frame["time_s"].to_numpy(dtype=float),
        buffer_tau_s,
    )
    frame["buffered_gt_target_mw"] = metrics["p_gt_mw"]
    frame["bess_power_mw"] = metrics["p_batt_mw"]
    frame["bess_energy_mwh"] = metrics["e_batt_mwh"]
    return {
        "bess_tau_s": float(buffer_tau_s),
        "bess_power_rating_mw": float(metrics["bess_power_mw"]),
        "bess_energy_kwh": float(metrics["bess_energy_mwh"]) * 1000.0,
        "bess_equivalent_duration_s": float(metrics["equivalent_duration_s"]),
        "bess_equivalent_c_rate": float(metrics["equivalent_c_rate"]),
        "buffered_gt_max_ramp_mw_s": float(metrics["gt_max_ramp_mw_s"]),
    }


def build_summary_row(
    schedule: ScheduleRun,
    turbine: TurbineRun,
    sample_dt_s: float,
    buffer_metrics: dict[str, float],
) -> dict[str, float | str]:
    jobs = schedule.jobs
    row: dict[str, float | str] = {
        "strategy": schedule.strategy.name,
        "strategy_label": schedule.strategy.label,
        "mean_full_start_delay_s": float(jobs["full_start_delay_s"].mean()),
        "p95_full_start_delay_s": float(
            jobs["full_start_delay_s"].quantile(0.95)
        ),
        "makespan_s": float(jobs["completion_s"].max()),
    }
    row.update(compute_ramp_metrics(schedule.timeseries))
    row.update(buffer_metrics)
    row.update(compute_turbine_metrics(turbine, sample_dt_s))
    return row


def _plot_series(ax, schedules: list[ScheduleRun], column: str) -> None:
    for schedule in schedules:
        ax.plot(
            schedule.timeseries["time_s"] / 60.0,
            schedule.timeseries[column],
            linewidth=1.25,
            label=schedule.strategy.label,
        )
    ax.grid(alpha=0.25)


def plot_power_and_ramp(schedules: list[ScheduleRun], output_dir: Path, dpi: int) -> Path:
    set_plot_style()
    fig, axes = plt.subplots(3, 1, figsize=(12.5, 9.0), sharex=True)
    _plot_series(axes[0], schedules, "active_gpus")
    axes[0].set_ylabel("Active GPUs")
    axes[0].set_title("Scheduler controls the rate of GPU activation")
    axes[0].legend(fontsize=8, ncol=2)
    _plot_series(axes[1], schedules, "facility_power_mw")
    axes[1].set_ylabel("Facility power (MW)")
    axes[1].set_title("The same work produces different facility ramps")
    _plot_series(axes[2], schedules, "ramp_mw_s")
    axes[2].axhspan(-0.5, 0.5, color="#2A9D8F", alpha=0.12,
                    label="±0.5 MW/s target")
    axes[2].set_xlabel("Simulation time (min)")
    axes[2].set_ylabel("Ramp (MW/s)")
    axes[2].set_title("Completion-aware admission also offsets ramp-down events")
    axes[2].legend(fontsize=8)
    fig.tight_layout()
    path = output_dir / "01_scheduler_power_ramp.png"
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_turbine_response(
    schedules: list[ScheduleRun],
    turbines: dict[str, TurbineRun],
    output_dir: Path,
    dpi: int,
) -> Path:
    set_plot_style()
    fig, axes = plt.subplots(3, 1, figsize=(12.5, 9.0), sharex=True)
    for schedule in schedules:
        result = turbines[schedule.strategy.name].dynamics
        label = schedule.strategy.label
        axes[0].plot(result.t_s / 60.0, result.freq_hz, linewidth=1.1, label=label)
        axes[1].plot(result.t_s / 60.0, result.exhaust_T_K - 273.15,
                     linewidth=1.1, label=label)
        torsion = turbines[schedule.strategy.name].torsion
        if torsion is not None:
            torque_t = np.asarray(torsion["time_s"], dtype=float)
            torque = np.asarray(torsion["torque_kNm"], dtype=float)
            view = slice(None, None, max(1, torque.size // 100_000))
            axes[2].plot(torque_t[view] / 60.0, torque[view],
                         linewidth=0.8, label=label)
    axes[0].axhspan(*FREQUENCY_BAND_HZ, color="#2A9D8F", alpha=0.12)
    axes[0].axhline(UNDERFREQUENCY_TRIP_HZ, color="#C44536", linestyle="--",
                    linewidth=1.0, label="57.8 Hz trip")
    axes[0].set_ylabel("Frequency (Hz)")
    axes[0].set_title("Ramp control improves frequency nadir and out-of-band time")
    axes[0].legend(fontsize=8, ncol=2)
    axes[1].set_ylabel("Estimated exhaust (°C)")
    axes[1].set_title("Screening estimate: lower ramp reduces exhaust-temperature cycling")
    axes[2].set_xlabel("Simulation time (min)")
    axes[2].set_ylabel("Per-shaft torque (kNm)")
    axes[2].set_title("Per-shaft torsional response")
    for ax in axes:
        ax.grid(alpha=0.25)
    fig.tight_layout()
    path = output_dir / "02_turbine_response.png"
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_tradeoffs(summary: pd.DataFrame, output_dir: Path, dpi: int) -> Path:
    set_plot_style()
    fig, axes = plt.subplots(2, 2, figsize=(12.5, 8.5))
    labels = summary["strategy_label"]
    colors = ["#264653", "#E76F51", "#F4A261", "#2A9D8F"]
    axes[0, 0].barh(labels, summary["max_abs_ramp_mw_s"], color=colors)
    axes[0, 0].set_xlabel("Maximum |dP/dt| (MW/s)")
    axes[0, 0].set_title("Facility ramp")
    axes[0, 1].barh(labels, summary["bess_power_rating_mw"], color=colors)
    axes[0, 1].set_xlabel("Fast-buffer rating (MW)")
    axes[0, 1].set_title("10 s BESS power requirement")
    axes[1, 0].scatter(
        summary["p95_full_start_delay_s"],
        summary["frequency_nadir_hz"],
        s=85,
        color=colors,
    )
    for _, row in summary.iterrows():
        axes[1, 0].annotate(
            row["strategy"],
            (row["p95_full_start_delay_s"], row["frequency_nadir_hz"]),
            xytext=(5, 4),
            textcoords="offset points",
            fontsize=8,
        )
    axes[1, 0].set_xlabel("p95 full-start delay (s)")
    axes[1, 0].set_ylabel("Frequency nadir (Hz)")
    axes[1, 0].set_title("Electrical benefit versus scheduling delay")
    axes[1, 1].scatter(
        summary["max_abs_ramp_mw_s"],
        summary["max_abs_temp_rate_k_s"],
        s=85,
        color=colors,
    )
    for _, row in summary.iterrows():
        axes[1, 1].annotate(
            row["strategy"],
            (row["max_abs_ramp_mw_s"], row["max_abs_temp_rate_k_s"]),
            xytext=(5, 4),
            textcoords="offset points",
            fontsize=8,
        )
    axes[1, 1].set_xlabel("Maximum |dP/dt| (MW/s)")
    axes[1, 1].set_ylabel("Maximum estimated |dT/dt| (K/s)")
    axes[1, 1].set_title("Temperature result is a screening correlation")
    for ax in axes.flat:
        ax.grid(alpha=0.25)
    fig.tight_layout()
    path = output_dir / "03_strategy_tradeoffs.png"
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_fast_buffer(
    schedules: list[ScheduleRun],
    buffer_tau_s: float,
    output_dir: Path,
    dpi: int,
) -> Path:
    set_plot_style()
    selected_names = ["immediate", "ramp-aware-0p5"]
    selected = [
        schedule for name in selected_names for schedule in schedules
        if schedule.strategy.name == name
    ]
    fig, axes = plt.subplots(3, 2, figsize=(13.0, 9.0), sharex="col")
    for column, schedule in enumerate(selected):
        frame = schedule.timeseries
        time_min = frame["time_s"] / 60.0
        axes[0, column].plot(
            time_min, frame["facility_power_mw"], color="#264653",
            linewidth=1.1, label="Facility demand",
        )
        axes[0, column].plot(
            time_min, frame["buffered_gt_target_mw"], color="#2A9D8F",
            linewidth=1.3, label="Buffered turbine target",
        )
        axes[0, column].set_title(schedule.strategy.label)
        axes[0, column].set_ylabel("Power (MW)")
        axes[0, column].legend(fontsize=8)

        axes[1, column].plot(
            time_min, frame["bess_power_mw"], color="#E76F51", linewidth=1.0
        )
        rating_mw = float(frame["bess_power_mw"].abs().max())
        axes[1, column].set_ylabel("BESS power (MW)")
        axes[1, column].set_title(f"Fast-buffer rating: {rating_mw:.2f} MW")

        energy_kwh = 1000.0 * (
            frame["bess_energy_mwh"] - frame["bess_energy_mwh"].min()
        )
        axes[2, column].plot(time_min, energy_kwh, color="#F4A261", linewidth=1.0)
        axes[2, column].set_xlabel("Simulation time (min)")
        axes[2, column].set_ylabel("Energy swing (kWh)")
        axes[2, column].set_title(f"Energy capacity: {energy_kwh.max():.1f} kWh")

    for ax in axes.flat:
        ax.grid(alpha=0.25)
    fig.suptitle(
        f"Scheduler smoothing reduces the fast buffer needed for a "
        f"{buffer_tau_s:g} s turbine target",
        fontsize=16,
    )
    fig.tight_layout()
    path = output_dir / "04_fast_buffer_comparison.png"
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return path


def write_outputs(
    schedules: list[ScheduleRun],
    turbines: dict[str, TurbineRun],
    summary: pd.DataFrame,
    output_dir: Path,
) -> None:
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(data_dir / "strategy_summary.csv", index=False)
    pd.concat([schedule.jobs for schedule in schedules], ignore_index=True).to_csv(
        data_dir / "job_delays.csv", index=False
    )
    for schedule in schedules:
        name = schedule.strategy.name
        schedule.timeseries.to_parquet(data_dir / f"{name}_scheduler.parquet", index=False)
        turbines[name].dynamics.as_dataframe().to_parquet(
            data_dir / f"{name}_turbine.parquet", index=False
        )


def print_summary(summary: pd.DataFrame, calibration: PowerCalibration) -> None:
    print(
        f"Frontier calibration: idle={calibration.facility_idle_mw:.2f} MW, "
        f"active-node increment={calibration.active_node_delta_mw * 1000:.3f} kW, "
        f"available nodes={calibration.available_nodes}"
    )
    columns = [
        "strategy",
        "max_abs_ramp_mw_s",
        "p99_event_abs_ramp_mw_s",
        "frequency_nadir_hz",
        "frequency_oob_s",
        "bess_power_rating_mw",
        "bess_energy_kwh",
        "max_abs_temp_rate_k_s",
        "p95_full_start_delay_s",
    ]
    print("\nCritical outputs:")
    print(summary[columns].to_string(index=False, float_format=lambda value: f"{value:.3f}"))
    print("\nTemperature is an estimated exhaust-gas screening signal, not metal temperature or life.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    parser.add_argument("--fleet-size", type=int, default=2)
    parser.add_argument("--sample-dt", type=float, default=0.05)
    parser.add_argument("--torsion-sample-rate", type=float, default=200.0)
    parser.add_argument("--buffer-tau", type=float, default=10.0)
    parser.add_argument("--dpi", type=int, default=180)
    parser.add_argument("--no-plots", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    calibration = load_frontier_power_calibration()
    jobs, base_active_nodes = demo_workload()
    schedules = [
        simulate_schedule(jobs, base_active_nodes, calibration, strategy)
        for strategy in default_strategies()
    ]

    turbines: dict[str, TurbineRun] = {}
    summary_rows = []
    for schedule in schedules:
        print(f"Running {schedule.strategy.label}...", flush=True)
        buffer = add_buffer_columns(schedule, args.buffer_tau)
        turbine = run_turbine(
            schedule,
            args.fleet_size,
            args.sample_dt,
            args.torsion_sample_rate,
        )
        turbines[schedule.strategy.name] = turbine
        summary_rows.append(
            build_summary_row(schedule, turbine, args.sample_dt, buffer)
        )

    summary = pd.DataFrame(summary_rows)
    args.outdir.mkdir(parents=True, exist_ok=True)
    write_outputs(schedules, turbines, summary, args.outdir)
    outputs: list[Path] = []
    if not args.no_plots:
        outputs = [
            plot_power_and_ramp(schedules, args.outdir, args.dpi),
            plot_turbine_response(schedules, turbines, args.outdir, args.dpi),
            plot_tradeoffs(summary, args.outdir, args.dpi),
            plot_fast_buffer(
                schedules, args.buffer_tau, args.outdir, args.dpi
            ),
        ]
    print_summary(summary, calibration)
    try:
        display_outdir = args.outdir.relative_to(ROOT)
    except ValueError:
        display_outdir = args.outdir
    print(f"\nData written to {display_outdir / 'data'}")
    for path in outputs:
        try:
            display_path = path.relative_to(ROOT)
        except ValueError:
            display_path = path
        print(f"Figure written to {display_path}")


if __name__ == "__main__":
    main()
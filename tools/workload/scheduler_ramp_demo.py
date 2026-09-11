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
import hashlib
import json
import sys
from dataclasses import asdict, dataclass, replace
from importlib.metadata import version
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
from tools.workload.power_asset_dispatch import (  # noqa: E402
    AssetSettings,
    dispatch_storage,
)


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
    flexible: bool = True


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


def demo_workload(scenario: str = "synchronized") -> tuple[list[JobSpec], int]:
    """Independent batch tasks; the fixed background represents protected service."""
    jobs = [
        JobSpec("batch-a", 120, 240, 1200),
        JobSpec("batch-b", 120, 240, 900),
        JobSpec("batch-c", 120, 240, 700),
        JobSpec("batch-d", 360, 240, 1100),
        JobSpec("batch-e", 360, 240, 900),
        JobSpec("batch-f", 360, 240, 700),
        JobSpec("batch-g", 600, 180, 1000),
        JobSpec("batch-h", 600, 180, 800),
        JobSpec("batch-i", 600, 180, 600),
    ]
    if scenario == "synchronized":
        return jobs, 1600
    if scenario == "irregular":
        durations = [190, 270, 310, 175, 265, 205, 180, 230, 150]
        return [replace(job, duration_s=duration)
                for job, duration in zip(jobs, durations)], 1600
    if scenario == "busy":
        return jobs, 6800
    raise ValueError(f"unknown workload scenario: {scenario}")


def default_strategies() -> list[Strategy]:
    return [
        Strategy("immediate", "Immediate activation", "immediate"),
        Strategy("wave-512", "Fixed wave: 512 nodes/s", "fixed",
                 activation_limit_nodes=512),
        Strategy("wave-256", "Fixed wave: 256 nodes/s", "fixed",
                 activation_limit_nodes=256),
        Strategy("wave-226", "Fixed wave: 226 nodes/s", "fixed",
                 activation_limit_nodes=226),
        Strategy("reuse-226", "Reuse + 226 new nodes/s", "ramp",
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
        budget_nodes = int(np.floor(strategy.ramp_limit_mw_s / node_delta_mw))
        if budget_nodes < 1:
            raise ValueError("ramp budget must permit at least one node per second")
        return min(free_nodes, budget_nodes)
    raise ValueError(f"unknown strategy mode: {strategy.mode}")


def simulate_schedule(
    jobs: list[JobSpec],
    base_active_nodes: int,
    calibration: PowerCalibration,
    strategy: Strategy,
    settle_s: int = 120,
) -> ScheduleRun:
    if (not isinstance(base_active_nodes, int)
            or not 0 <= base_active_nodes <= calibration.available_nodes
            or not isinstance(settle_s, int) or settle_s < 1):
        raise ValueError("invalid background capacity or settling duration")
    if (calibration.available_nodes < 1 or calibration.gpus_per_node < 1
            or not np.isfinite(calibration.facility_idle_mw)
            or calibration.facility_idle_mw < 0
            or not np.isfinite(calibration.active_node_delta_mw)
            or calibration.active_node_delta_mw <= 0):
        raise ValueError("invalid power calibration")
    if (strategy.mode == "fixed"
            and (not isinstance(strategy.activation_limit_nodes, int)
                 or strategy.activation_limit_nodes < 1)):
        raise ValueError("activation limit must be a positive integer")
    if (strategy.mode == "ramp"
            and (strategy.ramp_limit_mw_s is None
                 or not np.isfinite(strategy.ramp_limit_mw_s)
                 or strategy.ramp_limit_mw_s <= 0)):
        raise ValueError("ramp limit must be positive and finite")
    _activation_allowance(strategy, calibration.available_nodes,
                          calibration.active_node_delta_mw)
    jobs_by_id = {job.job_id: job for job in jobs}
    if len(jobs_by_id) != len(jobs):
        raise ValueError("job IDs must be unique")
    for job in jobs:
        if (not all(isinstance(value, int) for value in
                    (job.submit_s, job.duration_s, job.nodes))
                or job.submit_s < 1 or job.duration_s < 1 or job.nodes < 1):
            raise ValueError("jobs require positive integer arrival, duration, and nodes")
    if jobs and base_active_nodes == calibration.available_nodes:
        raise ValueError("no capacity available for submitted work")
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
    finished_at_s: int | None = None
    max_horizon_s = (max((job.submit_s for job in jobs), default=0)
                     + sum(job.nodes * job.duration_s for job in jobs) + settle_s)

    while current_s <= max_horizon_s:
        ending = [cohort for cohort in cohorts if cohort[0] <= current_s]
        cohorts = [cohort for cohort in cohorts if cohort[0] > current_s]
        completed_nodes = sum(cohort[2] for cohort in ending)
        for end_s, job_id, _ in ending:
            completion[job_id] = max(completion.get(job_id, 0), end_s)

        while (submit_index < len(ordered_jobs)
               and ordered_jobs[submit_index].submit_s <= current_s):
            pending.append(ordered_jobs[submit_index].job_id)
            submit_index += 1

        activated_nodes = 0
        powered_up_nodes = 0
        idled_nodes = completed_nodes

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

        active_elastic_nodes = sum(cohort[2] for cohort in cohorts)
        free_nodes = (calibration.available_nodes - base_active_nodes
                      - active_elastic_nodes)
        # Protected jobs must start in full at arrival, or the scenario is infeasible.
        protected = [job_id for job_id in pending if not jobs_by_id[job_id].flexible]
        if sum(remaining[job_id] for job_id in protected) > free_nodes:
            raise ValueError("insufficient capacity for immediate protected work")
        for job_id in protected:
            pending.remove(job_id)
            pending.insert(0, job_id)
            count = remaining[job_id]
            activated_nodes += activate_pending(count)
            free_nodes -= count
        allowance = _activation_allowance(
            strategy, free_nodes, calibration.active_node_delta_mw
        )
        if strategy.mode == "ramp":
            allowance = min(free_nodes, allowance + max(0, completed_nodes - activated_nodes))
        activated_nodes += activate_pending(allowance)
        reused_nodes = min(completed_nodes, activated_nodes)
        powered_up_nodes = activated_nodes - reused_nodes
        idled_nodes = completed_nodes - reused_nodes

        active_nodes = (
            base_active_nodes + sum(cohort[2] for cohort in cohorts)
        )
        rows.append({
            "time_s": current_s,
            "active_nodes": active_nodes,
            "active_gpus": active_nodes * calibration.gpus_per_node,
            "activated_nodes": activated_nodes,
            "powered_up_nodes": powered_up_nodes,
            "completed_nodes": completed_nodes,
            "idled_nodes": idled_nodes,
            "held_active_nodes": 0,
            "useful_elastic_nodes": active_nodes - base_active_nodes,
            "reused_nodes": reused_nodes,
            "pending_nodes": sum(remaining[job_id] for job_id in pending),
        })

        finished = (
            submit_index == len(ordered_jobs) and not pending and not cohorts
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
            "flexible": job.flexible,
            "first_start_s": first_start[job.job_id],
            "full_start_s": full_start[job.job_id],
            "completion_s": completion[job.job_id],
            "first_start_delay_s": first_start[job.job_id] - job.submit_s,
            "full_start_delay_s": full_start[job.job_id] - job.submit_s,
            "turnaround_s": completion[job.job_id] - job.submit_s,
        })
    columns = ["strategy", "job_id", "submit_s", "nodes", "duration_s", "flexible",
               "first_start_s", "full_start_s", "completion_s", "first_start_delay_s",
               "full_start_delay_s", "turnaround_s"]
    return ScheduleRun(strategy, frame, pd.DataFrame(job_rows, columns=columns))


def align_schedule_windows(schedules: list[ScheduleRun]) -> None:
    """Use [0, end) for energy/work; the final row is an endpoint, not an interval."""
    end_s = max(int(run.timeseries["time_s"].iloc[-1]) for run in schedules)
    for run in schedules:
        frame = run.timeseries.set_index("time_s")
        old_end = int(frame.index[-1])
        frame = frame.reindex(range(end_s + 1)).ffill()
        flow_columns = ["activated_nodes", "powered_up_nodes", "completed_nodes",
                        "idled_nodes", "reused_nodes", "ramp_mw_s"]
        frame.loc[frame.index > old_end, flow_columns] = 0
        run.timeseries = frame.rename_axis("time_s").reset_index()


def compute_ramp_metrics(frame: pd.DataFrame) -> dict[str, float]:
    ramp = frame["ramp_mw_s"].to_numpy(dtype=float)[1:]
    ramp_events = np.abs(ramp[np.abs(ramp) > 1e-12])
    power = frame["facility_power_mw"].to_numpy(dtype=float)
    ten_second_ramp = (power[10:] - power[:-10]) / 10.0
    if not ramp.size:
        ramp = np.zeros(1)
    return {
        "max_up_ramp_mw_s": float(np.max(ramp)),
        "max_down_ramp_mw_s": float(np.min(ramp)),
        "max_abs_ramp_mw_s": float(np.max(np.abs(ramp))),
        "p95_abs_ramp_mw_s": float(np.percentile(np.abs(ramp), 95)),
        "p99_abs_ramp_mw_s": float(np.percentile(np.abs(ramp), 99)),
        "p99_event_abs_ramp_mw_s": (
            float(np.percentile(ramp_events, 99)) if ramp_events.size else 0.0
        ),
        "max_10s_abs_ramp_mw_s": (
            float(np.max(np.abs(ten_second_ramp))) if ten_second_ramp.size else 0.0
        ),
        "seconds_above_1_mw_s": float(np.sum(np.abs(ramp) > 1.0)),
    }


def lm2500_dispatch_estimate(
    load_fraction: np.ndarray, fleet_size: int = 1,
) -> dict[str, np.ndarray]:
    """Screening exhaust estimate: ThermoPower shape, LM2500 full-load anchor."""
    plant = GasTurbinePlant(rated_power_mw=22.0)
    result = plant.dispatch(load_fraction)
    raw_full_load_k = float(plant.dispatch(1.0)["exhaust_T_K"])
    result["exhaust_T_K"] = (
        np.asarray(result["exhaust_T_K"], dtype=float)
        + LM2500_FULL_LOAD_EXHAUST_K - raw_full_load_k
    )
    for key in ("power_w", "fuel_kg_s", "exhaust_m_kg_s", "co2_kg_s"):
        result[key] = np.asarray(result[key]) * fleet_size
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
    load_mw: np.ndarray | None = None,
    include_torsion: bool = False,
) -> TurbineRun:
    time_s = schedule.timeseries["time_s"].to_numpy(dtype=float)
    if fleet_size < 1 or not np.isfinite(sample_dt_s) or sample_dt_s <= 0:
        raise ValueError("fleet size and sampling interval must be positive")
    if load_mw is None:
        load_mw = schedule.timeseries["facility_power_mw"].to_numpy(dtype=float)
    params = GGOV1Params.lm2500_overrides(
        Sn_mva=23.0 * fleet_size,
        Trate_mw=22.0 * fleet_size,
        alpha_load_damping=0.0,
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
        dispatch_fn=lambda fraction: lm2500_dispatch_estimate(fraction, fleet_size),
    )
    trip_s = float(np.sum(dynamics.freq_hz < UNDERFREQUENCY_TRIP_HZ) * sample_dt_s)
    if trip_s > 0:
        first_trip = int(np.flatnonzero(dynamics.freq_hz < UNDERFREQUENCY_TRIP_HZ)[0])
        # Protection ends the valid screening trajectory. Never publish the
        # unconstrained model's subsequent collapse/recovery as plant operation.
        for key, value in vars(dynamics).items():
            if isinstance(value, np.ndarray):
                setattr(dynamics, key, value[:first_trip + 1])
    torsion = None
    if include_torsion and trip_s == 0.0:
        torsion = run_torsion(dynamics, fleet_size, torsion_sample_rate_hz)
    return TurbineRun(dynamics, torsion)


def compute_turbine_metrics(run: TurbineRun, sample_dt_s: float) -> dict[str, float]:
    result = run.dynamics
    low_hz, high_hz = FREQUENCY_BAND_HZ
    temperature_k = np.asarray(result.exhaust_T_K, dtype=float)
    temperature_rate = np.diff(temperature_k) / np.diff(result.t_s)
    temperature_rate_events = np.abs(
        temperature_rate[np.abs(temperature_rate) > 1e-9]
    )
    metrics = {
        "frequency_nadir_hz": float(np.min(result.freq_hz)),
        "frequency_zenith_hz": float(np.max(result.freq_hz)),
        "frequency_oob_s": float(np.sum(
            np.diff(result.t_s) * ((result.freq_hz[:-1] < low_hz)
                                  | (result.freq_hz[:-1] > high_hz)))),
        "first_trip_s": (
            float(result.t_s[np.flatnonzero(result.freq_hz < UNDERFREQUENCY_TRIP_HZ)[0]])
            if np.any(result.freq_hz < UNDERFREQUENCY_TRIP_HZ) else np.nan),
        "exhaust_temp_min_c": float(np.min(temperature_k) - 273.15),
        "exhaust_temp_max_c": float(np.max(temperature_k) - 273.15),
        "exhaust_temp_range_k": float(np.ptp(temperature_k)),
        "max_abs_temp_rate_k_s": float(np.max(np.abs(temperature_rate))),
        "p99_abs_temp_rate_k_s": float(np.percentile(np.abs(temperature_rate), 99)),
        "p99_event_abs_temp_rate_k_s": float(
            np.percentile(temperature_rate_events, 99) if temperature_rate_events.size else 0.0
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
        "mean_full_start_delay_s": float(jobs["full_start_delay_s"].mean()) if len(jobs) else 0.0,
        "p95_full_start_delay_s": float(
            jobs["full_start_delay_s"].quantile(0.95)
        ) if len(jobs) else 0.0,
        "max_full_start_delay_s": float(jobs["full_start_delay_s"].max()) if len(jobs) else 0.0,
        "last_completion_s": float(jobs["completion_s"].max()) if len(jobs) else 0.0,
        "makespan_s": float(jobs["completion_s"].max() - jobs["submit_s"].min()) if len(jobs) else 0.0,
        "accounting_window_s": float(schedule.timeseries["time_s"].iloc[-1]),
        "useful_node_seconds": float(schedule.timeseries["useful_elastic_nodes"].iloc[:-1].sum()),
        "facility_energy_mwh": float(schedule.timeseries["facility_power_mw"].iloc[:-1].sum() / 3600),
        "nonproductive_hold_energy_mwh": 0.0,
        "generator_only_trajectory_valid": bool(
            np.all(turbine.dynamics.freq_hz >= UNDERFREQUENCY_TRIP_HZ)),
        "generator_only_fleet_fuel_kg": (
            float(turbine.dynamics.cum_fuel_kg[-1])
            if np.all(turbine.dynamics.freq_hz >= UNDERFREQUENCY_TRIP_HZ) else np.nan),
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
    axes[2].set_title("One-second power changes; shutdown ramps are not guaranteed")
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
        axes[2].plot(result.t_s / 60.0, result.Pm_pt_mw,
                     linewidth=1.1, label=label)
    axes[0].axhspan(*FREQUENCY_BAND_HZ, color="#2A9D8F", alpha=0.12)
    axes[0].axhline(UNDERFREQUENCY_TRIP_HZ, color="#C44536", linestyle="--",
                    linewidth=1.0, label="57.8 Hz trip")
    axes[0].set_ylabel("Frequency (Hz)")
    axes[0].set_title("Generator-only screening: traces stop at first protection crossing")
    axes[0].legend(fontsize=8, ncol=2)
    axes[1].set_ylabel("Estimated exhaust (°C)")
    axes[1].set_title("Estimated exhaust temperature, not component temperature or life")
    axes[2].set_xlabel("Simulation time (min)")
    axes[2].set_ylabel("Mechanical power (MW)")
    axes[2].set_title("Generator-only mechanical response")
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
    colors = ["#264653", "#E76F51", "#F4A261", "#2A9D8F", "#457B9D"]
    axes[0, 0].barh(labels, summary["max_abs_ramp_mw_s"], color=colors)
    axes[0, 0].set_xlabel("Maximum |dP/dt| (MW/s)")
    axes[0, 0].set_title("Facility ramp")
    axes[0, 1].barh(labels, summary["bess_power_rating_mw"], color=colors)
    axes[0, 1].set_xlabel("Fast-buffer rating (MW)")
    axes[0, 1].set_title("Ideal 10 s buffer estimate (not installed capacity)")
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
    axes[1, 1].barh(labels, summary["best_operating_cost"], color=colors)
    axes[1, 1].set_xlabel("Modeled fuel + storage use cost ($)")
    axes[1, 1].set_title("Best feasible tested dispatch; blank means infeasible")
    for ax in axes.flat:
        ax.grid(alpha=0.25)
    fig.tight_layout()
    path = output_dir / "03_strategy_tradeoffs.png"
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_fast_buffer(
    schedules: list[ScheduleRun],
    coordinated: dict[str, tuple[pd.DataFrame, TurbineRun]],
    output_dir: Path,
    dpi: int,
) -> Path:
    set_plot_style()
    selected_names = ["immediate", "wave-226"]
    selected = [
        schedule for name in selected_names for schedule in schedules
        if schedule.strategy.name == name
    ]
    fig, axes = plt.subplots(4, 2, figsize=(13.0, 11.0), sharex="col")
    for column, schedule in enumerate(selected):
        if schedule.strategy.name not in coordinated:
            axes[0, column].set_title(f"{schedule.strategy.label}: no feasible policy")
            continue
        frame = schedule.timeseries
        dispatch, turbine = coordinated[schedule.strategy.name]
        time_min = frame["time_s"] / 60.0
        axes[0, column].plot(
            time_min, frame["facility_power_mw"], color="#264653",
            linewidth=1.1, label="Facility demand",
        )
        axes[0, column].plot(
            time_min, dispatch["generator_electrical_load_mw"], color="#2A9D8F",
            linewidth=1.3, label="Generator electrical load",
        )
        axes[0, column].plot(
            turbine.dynamics.t_s / 60, turbine.dynamics.Pm_pt_mw,
            linewidth=0.9, linestyle="--", label="Generator mechanical power",
        )
        axes[0, column].set_title(schedule.strategy.label)
        axes[0, column].set_ylabel("Power (MW)")
        axes[0, column].legend(fontsize=8)

        axes[1, column].plot(
            time_min, dispatch["battery_power_mw"], color="#E76F51", linewidth=1.0
        )
        rating_mw = float(dispatch["battery_power_mw"].abs().max())
        axes[1, column].set_ylabel("BESS power (MW)")
        axes[1, column].set_title(f"Actual storage peak: {rating_mw:.2f} MW")

        axes[2, column].plot(time_min, dispatch["state_of_charge"] * 100,
                             color="#F4A261", linewidth=1.0)
        axes[2, column].set_ylabel("Stored charge (%)")
        axes[2, column].set_title("Initial charge restored; installed bounds 10–90%")
        axes[3, column].plot(turbine.dynamics.t_s / 60, turbine.dynamics.freq_hz,
                             color="#457B9D", linewidth=1.0)
        axes[3, column].axhspan(*FREQUENCY_BAND_HZ, color="#2A9D8F", alpha=0.12)
        axes[3, column].set_ylabel("Frequency (Hz)")
        axes[3, column].set_xlabel("Simulation time (min)")
        axes[3, column].set_title("Coordinated system frequency")

    for ax in axes.flat:
        ax.grid(alpha=0.25)
    fig.suptitle(
        "Same installed hardware, best feasible tested power policy for each schedule",
        fontsize=16,
    )
    fig.tight_layout()
    path = output_dir / "04_fast_buffer_comparison.png"
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return path


def compare_asset_policies(
    schedule: ScheduleRun,
    settings: AssetSettings,
    fleet_size: int,
    sample_dt_s: float,
    cache: dict[tuple[int, float, bytes, bytes], TurbineRun],
    taus: tuple[float, ...] = (0.0, 2.0, 5.0, 10.0, 20.0),
) -> tuple[pd.DataFrame, tuple[pd.DataFrame, TurbineRun] | None]:
    """Minimize cost over the same finite candidate set, after feasibility screening."""
    params = GGOV1Params.lm2500_overrides(Trate_mw=22.0 * fleet_size)
    ceiling = params.Trate_mw * params.Pm_thermal_max_pu
    demand = schedule.timeseries["facility_power_mw"].to_numpy(dtype=float)
    rows = []
    best = None
    best_cost = np.inf
    for tau in taus:
        frame, metrics = dispatch_storage(demand, tau, settings, ceiling)
        reasons = []
        for key in ("generator_ramp_violation_s", "generator_reserve_violation_s"):
            if metrics[key] > 0:
                reasons.append(key)
        if abs(metrics["terminal_energy_error_kwh"]) > 1e-6:
            reasons.append("terminal_charge")
        net = frame["generator_electrical_load_mw"].to_numpy()
        turbine = None
        # Always retain the no-storage dynamic reference; reject other candidates
        # cheaply when they already fail the common engineering constraints.
        if (not reasons or tau == 0) and np.max(net) <= ceiling:
            key = (fleet_size, sample_dt_s,
                   schedule.timeseries["time_s"].to_numpy(dtype=float).tobytes(),
                   net.tobytes())
            if key not in cache:
                cache[key] = run_turbine(schedule, fleet_size, sample_dt_s, 200.0,
                                         load_mw=net)
            turbine = cache[key]
            dynamics = turbine.dynamics
            metrics.update({
                "frequency_nadir_hz": float(dynamics.freq_hz.min()),
                "frequency_zenith_hz": float(dynamics.freq_hz.max()),
                "fleet_fuel_kg": float(dynamics.cum_fuel_kg[-1]),
                "mechanical_peak_mw": float(dynamics.Pm_pt_mw.max()),
            })
            if np.any(dynamics.freq_hz < UNDERFREQUENCY_TRIP_HZ):
                reasons.append("underfrequency_trip")
                metrics["fleet_fuel_kg"] = np.nan
            if (metrics["frequency_nadir_hz"] < FREQUENCY_BAND_HZ[0]
                    or metrics["frequency_zenith_hz"] > FREQUENCY_BAND_HZ[1]):
                reasons.append("frequency_band")
            if np.max(dynamics.Pm_hp_mw) > ceiling + 1e-6:
                reasons.append("mechanical_ceiling")
            metrics["operating_cost"] = (
                metrics["fleet_fuel_kg"] * settings.fuel_cost_per_kg
                + metrics["battery_throughput_mwh"] * settings.battery_cost_per_mwh
            )
        else:
            metrics.update({key: np.nan for key in (
                "frequency_nadir_hz", "frequency_zenith_hz", "fleet_fuel_kg",
                "mechanical_peak_mw", "operating_cost")})
        feasible = not reasons and turbine is not None
        rows.append({
            "strategy": schedule.strategy.name, **metrics,
            "dynamic_screened": turbine is not None,
            "feasible": feasible, "rejection_reason": ";".join(reasons),
        })
        if feasible and metrics["operating_cost"] < best_cost:
            best_cost = metrics["operating_cost"]
            best = (frame, turbine)
    return pd.DataFrame(rows), best


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
        "max_full_start_delay_s",
        "best_operating_cost",
        "best_tau_s",
    ]
    print("\nCritical outputs:")
    print(summary[columns].to_string(index=False, float_format=lambda value: f"{value:.3f}"))
    print("\nTemperature is an estimated exhaust-gas screening signal, not metal temperature or life.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    parser.add_argument("--fleet-size", type=int, default=2)
    parser.add_argument("--sample-dt", type=float, default=0.05)
    parser.add_argument("--buffer-tau", type=float, default=10.0)
    parser.add_argument("--dpi", type=int, default=180)
    parser.add_argument("--no-plots", action="store_true")
    parser.add_argument("--scenarios", nargs="+",
                        choices=["synchronized", "irregular", "busy"],
                        default=["synchronized", "irregular", "busy"])
    parser.add_argument("--no-sensitivity", action="store_true")
    parser.add_argument("--delay-limits", nargs="+", type=float, default=[0, 5, 15])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if (args.fleet_size < 1 or not np.isfinite(args.sample_dt)
            or not 0 < args.sample_dt <= 0.1 or args.dpi < 1
            or not np.isfinite(args.buffer_tau) or args.buffer_tau <= 0
            or any(not np.isfinite(limit) or limit < 0 for limit in args.delay_limits)):
        raise ValueError("invalid fleet, sampling, plotting, buffer, or delay setting")
    calibration = load_frontier_power_calibration()
    settings = AssetSettings()
    hardware = {"6MW-1p5MWh": settings}
    if not args.no_sensitivity:
        hardware.update({
            "2MW-0p5MWh": replace(settings, battery_power_mw=2, battery_energy_mwh=0.5),
            "4MW-1MWh": replace(settings, battery_power_mw=4, battery_energy_mwh=1),
        })
    all_summaries, sensitivity = [], []
    args.outdir.mkdir(parents=True, exist_ok=True)
    for scenario in dict.fromkeys(args.scenarios):
        jobs, base_active_nodes = demo_workload(scenario)
        schedules = [
            simulate_schedule(jobs, base_active_nodes, calibration, strategy,
                              settle_s=2 * settings.recovery_s)
            for strategy in default_strategies()
        ]
        align_schedule_windows(schedules)
        baseline_jobs = schedules[0].jobs.set_index("job_id")
        baseline_completion = float(baseline_jobs["completion_s"].max())
        scenario_dir = args.outdir if scenario == "synchronized" else args.outdir / scenario
        (scenario_dir / "data").mkdir(parents=True, exist_ok=True)
        turbines, coordinated, summary_rows, candidates = {}, {}, [], []
        cache: dict[tuple[int, float, bytes, bytes], TurbineRun] = {}
        for schedule in schedules:
            print(f"{scenario}: {schedule.strategy.label}", flush=True)
            delays = (schedule.jobs.set_index("job_id")["full_start_s"]
                      - baseline_jobs["full_start_s"])
            max_extra_delay = max(0.0, float(delays.max()))
            ideal_buffer = add_buffer_columns(schedule, args.buffer_tau)
            per_hardware = {}
            for hardware_name, asset in hardware.items():
                options, best = compare_asset_policies(
                    schedule, asset, args.fleet_size, args.sample_dt, cache)
                options["scenario"] = scenario
                options["hardware"] = hardware_name
                options["max_additional_delay_s"] = max_extra_delay
                candidates.append(options)
                per_hardware[hardware_name] = options
                if hardware_name == "6MW-1p5MWh" and best is not None:
                    coordinated[schedule.strategy.name] = best
                    best[0].to_parquet(
                        scenario_dir / "data" / f"{schedule.strategy.name}_coordinated.parquet",
                        index=False)
                    best[1].dynamics.as_dataframe().to_parquet(
                        scenario_dir / "data" / f"{schedule.strategy.name}_coordinated_turbine.parquet",
                        index=False)
                elif hardware_name == "6MW-1p5MWh":
                    for suffix in ("coordinated", "coordinated_turbine"):
                        (scenario_dir / "data" /
                         f"{schedule.strategy.name}_{suffix}.parquet").unlink(missing_ok=True)
            turbine = cache.get((
                args.fleet_size, args.sample_dt,
                schedule.timeseries["time_s"].to_numpy(dtype=float).tobytes(),
                schedule.timeseries["facility_power_mw"].to_numpy(dtype=float).tobytes(),
            ))
            if turbine is None:
                raise ValueError("generator-only reference exceeds fleet rating; use a larger fleet")
            turbines[schedule.strategy.name] = turbine
            row = build_summary_row(schedule, turbine, args.sample_dt, ideal_buffer)
            row.update({
                "scenario": scenario,
                "peak_active_nodes": float(schedule.timeseries["active_nodes"].max()),
                "max_additional_delay_s": max_extra_delay,
                "within_max_delay_allowance": max_extra_delay <= max(args.delay_limits),
                "completion_extension_s": float(schedule.jobs["completion_s"].max()) - baseline_completion,
            })
            feasible = per_hardware["6MW-1p5MWh"].query("feasible").sort_values(
                ["operating_cost", "tau_s"])
            row["feasible_policy_count"] = len(feasible)
            for key in ("operating_cost", "tau_s", "fleet_fuel_kg", "battery_peak_mw",
                        "battery_loss_mwh", "battery_throughput_mwh", "frequency_nadir_hz",
                        "frequency_zenith_hz", "terminal_energy_error_kwh"):
                row[f"best_{key}"] = float(feasible.iloc[0][key]) if len(feasible) else np.nan
            summary_rows.append(row)
        summary = pd.DataFrame(summary_rows)
        candidate_frame = pd.concat(candidates, ignore_index=True)
        candidate_frame.to_csv(scenario_dir / "data" / "dispatch_candidates.csv", index=False)
        for hardware_name in hardware:
            for allowance in args.delay_limits:
                eligible = candidate_frame[
                    candidate_frame["feasible"]
                    & (candidate_frame["hardware"] == hardware_name)
                    & (candidate_frame["max_additional_delay_s"] <= allowance)
                ].sort_values(["operating_cost", "strategy", "tau_s"])
                record = {"scenario": scenario, "hardware": hardware_name,
                          "delay_allowance_s": allowance, "feasible": bool(len(eligible))}
                for key in ("strategy", "tau_s", "operating_cost", "fleet_fuel_kg",
                            "battery_throughput_mwh", "max_additional_delay_s"):
                    record[key] = eligible.iloc[0][key] if len(eligible) else np.nan
                sensitivity.append(record)
        write_outputs(schedules, turbines, summary, scenario_dir)
        if not args.no_plots:
            plot_power_and_ramp(schedules, scenario_dir, args.dpi)
            plot_turbine_response(schedules, turbines, scenario_dir, args.dpi)
            plot_tradeoffs(summary, scenario_dir, args.dpi)
            plot_fast_buffer(schedules, coordinated, scenario_dir, args.dpi)
        print_summary(summary, calibration)
        all_summaries.append(summary)
    data_dir = args.outdir / "data"
    data_dir.mkdir(exist_ok=True)
    pd.concat(all_summaries, ignore_index=True).to_csv(
        data_dir / "scenario_comparison.csv", index=False)
    pd.DataFrame(sensitivity).to_csv(data_dir / "sensitivity.csv", index=False)
    sources = [Path(__file__), ROOT / "tools/workload/power_asset_dispatch.py",
               ROOT / "gas_plant/dynamics/ggov1.py", ROOT / "gas_plant/dynamics/multishaft.py",
               ROOT / "gas_plant/unit.py", ROOT / "gas_plant/data/gas_turbine_surrogate.csv",
               ROOT / "RAPS/config/frontier/system.json", ROOT / "RAPS/config/frontier/power.json"]
    manifest = {
        "objective": "lowest fuel + storage-throughput cost among feasible tested policies",
        "schema_version": 2,
        "scope": "fixed online equal-sharing fleet; no unit commitment or capital-cost optimization",
        "arguments": {**vars(args), "outdir": str(args.outdir)},
        "assets": {name: asdict(asset) for name, asset in hardware.items()},
        "calibration": asdict(calibration), "candidate_tau_s": [0, 2, 5, 10, 20],
        "versions": {"python": sys.version, **{
            package: version(package)
            for package in ("numpy", "pandas", "scipy", "matplotlib", "pyarrow")
        }},
        "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in sources},
    }
    (data_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\nStudy outputs: {args.outdir}")


if __name__ == "__main__":
    main()
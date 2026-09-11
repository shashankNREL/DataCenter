from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.workload.scheduler_ramp_demo import (
    add_buffer_columns,
    compute_ramp_metrics,
    default_strategies,
    demo_workload,
    load_frontier_power_calibration,
    simulate_schedule,
)


def build_schedule_runs():
    calibration = load_frontier_power_calibration()
    jobs, base_active_nodes = demo_workload()
    runs = [
        simulate_schedule(jobs, base_active_nodes, calibration, strategy)
        for strategy in default_strategies()
    ]
    return calibration, jobs, runs


def test_frontier_power_calibration():
    calibration = load_frontier_power_calibration()
    assert calibration.available_nodes == 9472
    assert calibration.gpus_per_node == 4
    assert calibration.active_node_delta_mw == pytest.approx(0.00220876, rel=1e-5)
    assert calibration.facility_idle_mw == pytest.approx(7.3065, rel=1e-4)


def test_strategies_schedule_same_work_and_control_ramp():
    calibration, jobs, runs = build_schedule_runs()
    expected_nodes = sum(job.nodes for job in jobs)
    ramp_metrics = {
        run.strategy.name: compute_ramp_metrics(run.timeseries)
        for run in runs
    }

    for run in runs:
        assert int(run.timeseries["activated_nodes"].sum()) == expected_nodes
        assert run.jobs["completion_s"].notna().all()
        assert run.timeseries["held_active_nodes"].iloc[-1] == 0

    ramp_aware = ramp_metrics["ramp-aware-0p5"]
    runs_by_name = {run.strategy.name: run for run in runs}
    assert ramp_aware["max_abs_ramp_mw_s"] <= (
        0.5 + calibration.active_node_delta_mw
    )
    assert ramp_aware["max_abs_ramp_mw_s"] < (
        ramp_metrics["immediate"]["max_abs_ramp_mw_s"] / 10.0
    )
    assert runs_by_name["ramp-aware-0p5"].timeseries[
        "facility_power_mw"
    ].max() <= runs_by_name["immediate"].timeseries["facility_power_mw"].max()


def test_scheduler_smoothing_reduces_fast_buffer_power():
    _, _, runs = build_schedule_runs()
    buffer_power = {
        run.strategy.name: add_buffer_columns(run, buffer_tau_s=10.0)[
            "bess_power_rating_mw"
        ]
        for run in runs
    }
    assert buffer_power["wave-256"] < buffer_power["immediate"]
    assert buffer_power["ramp-aware-0p5"] < buffer_power["wave-256"]
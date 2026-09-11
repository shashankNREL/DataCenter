from __future__ import annotations

import sys
from pathlib import Path

import pytest
import numpy as np
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.workload.scheduler_ramp_demo import (
    add_buffer_columns,
    align_schedule_windows,
    compare_asset_policies,
    compute_ramp_metrics,
    default_strategies,
    demo_workload,
    load_frontier_power_calibration,
    simulate_schedule,
    JobSpec,
    Strategy,
    lm2500_dispatch_estimate,
)
from tools.workload.power_asset_dispatch import AssetSettings, dispatch_storage


def build_schedule_runs(scenario="synchronized"):
    calibration = load_frontier_power_calibration()
    jobs, base_active_nodes = demo_workload(scenario)
    runs = [
        simulate_schedule(jobs, base_active_nodes, calibration, strategy)
        for strategy in default_strategies()
    ]
    align_schedule_windows(runs)
    return calibration, jobs, runs


def test_frontier_power_calibration():
    calibration = load_frontier_power_calibration()
    assert calibration.available_nodes == 9472
    assert calibration.gpus_per_node == 4
    assert calibration.active_node_delta_mw == pytest.approx(0.00220876, rel=1e-5)
    assert calibration.facility_idle_mw == pytest.approx(7.3065, rel=1e-4)


@pytest.mark.parametrize("scenario", ["synchronized", "irregular", "busy"])
def test_strategies_schedule_same_work_and_control_ramp(scenario):
    calibration, jobs, runs = build_schedule_runs(scenario)
    expected_nodes = sum(job.nodes for job in jobs)
    ramp_metrics = {
        run.strategy.name: compute_ramp_metrics(run.timeseries)
        for run in runs
    }

    for run in runs:
        assert int(run.timeseries["activated_nodes"].sum()) == expected_nodes
        assert run.jobs["completion_s"].notna().all()
        assert run.timeseries["held_active_nodes"].iloc[-1] == 0
        assert run.timeseries["held_active_nodes"].sum() == 0
        assert run.timeseries["active_nodes"].max() <= calibration.available_nodes
        assert run.timeseries["useful_elastic_nodes"].iloc[:-1].sum() == sum(
            job.nodes * job.duration_s for job in jobs)
        assert (run.jobs["first_start_s"] >= run.jobs["submit_s"]).all()
        assert (run.jobs["completion_s"] - run.jobs["full_start_s"]
                == run.jobs["duration_s"]).all()
        assert run.timeseries["time_s"].iloc[-1] == runs[0].timeseries["time_s"].iloc[-1]
        assert run.timeseries["facility_power_mw"].iloc[:-1].sum() == pytest.approx(
            runs[0].timeseries["facility_power_mw"].iloc[:-1].sum())

    ramp_aware = ramp_metrics["reuse-226"]
    assert ramp_aware["max_up_ramp_mw_s"] <= (
        0.5 + calibration.active_node_delta_mw
    )


def test_scheduler_smoothing_reduces_fast_buffer_power():
    _, _, runs = build_schedule_runs()
    buffer_power = {
        run.strategy.name: add_buffer_columns(run, buffer_tau_s=10.0)[
            "bess_power_rating_mw"
        ]
        for run in runs
    }
    assert buffer_power["wave-256"] < buffer_power["immediate"]
    assert buffer_power["wave-226"] < buffer_power["wave-256"]
    # Reuse plus new starts overlaps old and new waves: it is not guaranteed
    # to outperform the simpler matched rule.
    assert buffer_power["reuse-226"] > buffer_power["wave-226"]


def test_matched_reuse_does_not_claim_a_shutdown_guarantee():
    calibration = load_frontier_power_calibration()
    # Reuse allows more than 226 useful starts, but never holds finished work active.
    jobs = [JobSpec("first", 1, 5, 452), JobSpec("replacement", 6, 2, 678)]
    run = simulate_schedule(jobs, 0, calibration, default_strategies()[-1])
    assert run.timeseries["activated_nodes"].max() > 226
    assert run.timeseries["held_active_nodes"].sum() == 0
    assert compute_ramp_metrics(run.timeseries)["max_down_ramp_mw_s"] < -0.5


def test_protected_work_starts_immediately_or_fails_explicitly():
    calibration = replace(load_frontier_power_calibration(), available_nodes=20)
    jobs = [JobSpec("flexible", 1, 10, 5), JobSpec("service", 2, 3, 10, flexible=False)]
    run = simulate_schedule(jobs, 0, calibration,
                            Strategy("slow", "slow", "fixed", activation_limit_nodes=1))
    assert run.jobs.set_index("job_id").loc["service", "full_start_delay_s"] == 0
    with pytest.raises(ValueError, match="protected"):
        simulate_schedule(jobs, 15, calibration, default_strategies()[0])


@pytest.mark.parametrize("limit", [0, -1, 0.001, np.nan, np.inf])
def test_invalid_ramp_budget_is_not_rounded_up(limit):
    with pytest.raises(ValueError):
        simulate_schedule([], 0, load_frontier_power_calibration(),
                          Strategy("bad", "bad", "ramp", ramp_limit_mw_s=limit))


def test_empty_workload_and_constant_ramp_metrics():
    run = simulate_schedule([], 0, load_frontier_power_calibration(),
                            default_strategies()[0], settle_s=1)
    assert run.jobs.empty
    assert all(value == 0 for value in compute_ramp_metrics(run.timeseries).values())
    assert all(value == 0 for value in compute_ramp_metrics(run.timeseries.iloc[:1]).values())


def test_invalid_jobs_and_capacity():
    calibration = load_frontier_power_calibration()
    strategy = default_strategies()[0]
    for jobs in ([JobSpec("a", 0, 1, 1)], [JobSpec("a", 1, 0, 1)],
                 [JobSpec("a", 1, 1, 0)], [JobSpec("a", 1, 1, 1)] * 2):
        with pytest.raises(ValueError):
            simulate_schedule(jobs, 0, calibration, strategy)
    with pytest.raises(ValueError, match="capacity"):
        simulate_schedule([JobSpec("a", 1, 1, 1)], calibration.available_nodes,
                          calibration, strategy)


def test_fleet_reporting_scales_extensive_not_intensive_quantities():
    single = lm2500_dispatch_estimate(np.array([0.2, 0.5]), fleet_size=1)
    fleet = lm2500_dispatch_estimate(np.array([0.2, 0.5]), fleet_size=3)
    for key in ("fuel_kg_s", "power_w", "co2_kg_s", "exhaust_m_kg_s"):
        np.testing.assert_allclose(fleet[key], 3 * single[key])
    np.testing.assert_allclose(fleet["exhaust_T_K"], single["exhaust_T_K"])


def test_storage_energy_power_efficiency_and_recovery():
    settings = AssetSettings(recovery_s=10)
    demand = np.r_[np.full(20, 10.0), np.full(20, 16.0), np.full(41, 10.0)]
    frame, metrics = dispatch_storage(demand, 10, settings, 44)
    battery = frame["battery_power_mw"].to_numpy()
    stored = frame["stored_energy_mwh"].to_numpy()
    np.testing.assert_allclose(frame["generator_electrical_load_mw"] + battery, demand)
    np.testing.assert_allclose(
        np.diff(stored),
        (np.maximum(-battery[:-1], 0) * 0.95 - np.maximum(battery[:-1], 0) / 0.95) / 3600,
        atol=1e-14)
    assert frame["state_of_charge"].between(settings.min_soc, settings.max_soc).all()
    assert np.abs(battery).max() <= settings.battery_power_mw
    assert metrics["terminal_energy_error_kwh"] == pytest.approx(0, abs=1e-8)
    assert metrics["battery_loss_mwh"] > 0
    assert metrics["battery_charge_mwh"] - metrics["battery_discharge_mwh"] == pytest.approx(
        metrics["battery_loss_mwh"], abs=1e-10)
    assert metrics["power_balance_error_mw"] == 0


def test_storage_saturation_is_physical_and_reports_unmet_ramp():
    settings = AssetSettings(battery_power_mw=0.1, battery_energy_mwh=0.001, recovery_s=10)
    demand = np.r_[np.full(10, 10.0), np.full(40, 16.0), np.full(41, 10.0)]
    frame, metrics = dispatch_storage(demand, 20, settings, 44)
    assert metrics["battery_saturation_s"] > 0
    assert metrics["generator_ramp_violation_s"] > 0
    assert frame["state_of_charge"].min() >= settings.min_soc - 1e-12
    assert frame["state_of_charge"].max() <= settings.max_soc + 1e-12


def test_incomplete_recovery_and_reserve_are_reported():
    settings = AssetSettings(recovery_s=1, battery_power_mw=0.1)
    demand = np.r_[10.0, np.full(30, 16.0), 10.0, 10.0]
    _, metrics = dispatch_storage(demand, 20, settings, 17)
    assert abs(metrics["terminal_energy_error_kwh"]) > 1e-6
    assert metrics["generator_reserve_violation_s"] > 0


def test_storage_validation_and_no_storage_reference():
    with pytest.raises(ValueError):
        AssetSettings(charge_efficiency=0)
    with pytest.raises(ValueError):
        AssetSettings(initial_soc=1)
    settings = AssetSettings(recovery_s=2)
    with pytest.raises(ValueError, match="recovery"):
        dispatch_storage(np.arange(10.0), 5, settings, 44)
    demand = np.r_[10.0, 14.0, 10.0, 10.0, 10.0]
    frame, metrics = dispatch_storage(demand, 0, settings, 44)
    np.testing.assert_array_equal(frame["generator_electrical_load_mw"], demand)
    assert metrics["battery_peak_mw"] == 0


def test_coordinated_replay_and_finite_cost_selection():
    calibration = load_frontier_power_calibration()
    run = simulate_schedule([JobSpec("batch", 5, 10, 400)], 1600, calibration,
                            default_strategies()[0], settle_s=20)
    settings = AssetSettings(recovery_s=5, generator_ramp_mw_s=0.2)
    candidates, best = compare_asset_policies(run, settings, 2, 0.05, {}, taus=(0, 5, 10))
    assert not candidates.iloc[0]["feasible"]
    assert best is not None
    dispatch, turbine = best
    # The dynamic model must see residual electrical demand, with no fictitious
    # frequency-sensitive workload reduction.
    np.testing.assert_allclose(turbine.dynamics.Pe_mw, turbine.dynamics.Pe_demand_mw)
    assert turbine.dynamics.Pe_mw.max() < run.timeseries["facility_power_mw"].max()
    cost = (turbine.dynamics.cum_fuel_kg[-1] * settings.fuel_cost_per_kg
            + np.abs(dispatch["battery_power_mw"].iloc[:-1]).sum() / 3600
            * settings.battery_cost_per_mwh)
    assert cost == pytest.approx(candidates.loc[candidates["feasible"], "operating_cost"].min())
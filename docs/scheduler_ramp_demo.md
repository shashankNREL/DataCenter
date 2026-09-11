# Scheduler-controlled GPU ramp demonstration

## Purpose

This screening study tests whether systematic activation and idling of GPU node
groups can reduce the electrical ramp presented to an islanded gas-turbine
fleet. It is intended to demonstrate research potential, not a deployable
scheduler or an OEM-qualified turbine-life model.

Run from the repository root:

```bash
pixi run scheduler-ramp-demo
```

Outputs are written to `Presentation/scheduler_ramp_demo/`.

## Strategies

The deterministic workload has three synchronized waves of elastic jobs. Each
Frontier node represents four GPUs. Every strategy schedules the same 7,900
node cohorts for the same requested durations.

| Strategy | Control action |
|---|---|
| Immediate activation | Activates or idles all available cohorts in one second. |
| Fixed wave: 512 nodes/s | Limits admission to 512 nodes per second. |
| Fixed wave: 256 nodes/s | Limits admission to 256 nodes per second. |
| Completion-aware: 0.5 MW/s | Converts the electrical ramp budget to a node budget, reuses still-active completed nodes for queued work, and idles unused nodes in budgeted waves. |

The experiment assumes jobs are malleable enough to start worker cohorts at
different times. Applying the same control to strict gang-scheduled jobs would
require whole-job admission instead.

## Power and turbine coupling

The facility calculation uses the Frontier topology and component values in
`RAPS/config/frontier/`. The pinned calibration is:

- 9,472 available nodes after the missing rack is removed.
- 7.307 MW modeled facility idle power.
- 2.209 kW added when one node moves from CPU/GPU idle to full utilization.

Each strategy's one-second facility-power trace drives a two-unit LM2500 fleet
through the GGOV1 multishaft model. The study then computes per-shaft torque and
torsional fatigue for trajectories that remain above the 57.8 Hz trip level.

The 10-second BESS split uses a first-order low-pass target. Battery power is
the fast mismatch between facility demand and that target; battery energy is
the integrated mismatch. This is a comparative fast-buffer estimate and does
not include state-of-charge management, inverter limits, or reserve margin.

## Critical outputs

`data/strategy_summary.csv` reports, for every strategy:

- Maximum, p95, p99, and event-conditioned p99 absolute ramp rate.
- Maximum 10-second average ramp and seconds above 1 MW/s.
- Frequency nadir and zenith, time outside 59.4-60.6 Hz, and time below 57.8 Hz.
- Required BESS power, energy swing, equivalent duration, and buffered target ramp.
- Per-shaft torque range plus HCF and LCF screening damage.
- Estimated exhaust-temperature range, maximum and p99 temperature slew, and total temperature variation.
- Mean and p95 full-start delay and makespan.

The Parquet files retain the scheduler and turbine time series for further
analysis. The figures compare activation and ramp, turbine response, the
tradeoff between electrical benefit and scheduling delay, and the BESS power
and energy time series before and after scheduler smoothing.

## Temperature interpretation

The temperature trace is an **estimated exhaust-gas screening signal**. The
existing GGOV1 filtered fuel-temperature proxy is mapped through the available
ThermoPower part-load exhaust-temperature curve, shifted to the documented
LM2500 full-load exhaust point of 791 K.

Peak `|dT/dt|` can show whether slower electrical ramps also reduce acute
thermal excursions. It is not blade, combustor-liner, or transition-piece metal
temperature. Total temperature variation is reported alongside peak slew
because staging can lower acute thermal shock while extending the duration or
number of smaller cycles.

Do not convert these results into hot-gas-path life extension without a
calibrated exhaust-thermocouple model, heat transfer into component metal,
cooling-flow dynamics, geometry, material properties, and validated
temperature-cycle damage curves.

## Next research steps

1. Replay selected windows from `data/scheduler_data.csv` after validating its
   `time_start`, `time_end`, and `gres_alloc` schema.
2. Replace elastic cohort admission with whole-job and SLA-aware variants.
3. Co-optimize scheduler ramp limits and BESS power/state of charge.
4. Calibrate exhaust and component-metal thermal states against LM2500 data.
5. Evaluate fairness, starvation, throughput, and prediction uncertainty over
   longer workload ensembles.
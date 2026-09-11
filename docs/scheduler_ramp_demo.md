# Small scheduling changes, coordinated power assets

## Purpose

The question is whether starting **flexible batch work a few seconds apart**
can make an islanded data center easier to power, without changing the amount
of useful work. The scheduler is deliberately simple. Power management is
evaluated separately, using the same generator fleet, storage hardware, limits,
and candidate operating rules for every schedule.

“Optimal” here means **lowest modeled fuel-plus-storage-use cost among the
tested feasible policies**. It does not mean a globally optimal scheduler,
economic unit commitment, or an optimized battery purchase. The two generators
remain online and share load equally. This is an educational screening study,
not a validated plant design or a guarantee about production AI workloads.

The existing Pixi task runs the complete study:

```bash
pixi run scheduler-ramp-demo
```

```bash
python /home/runner/work/DataCenter/DataCenter/tools/workload/scheduler_ramp_demo.py
```

The second command is equivalent when the existing Python dependencies are
already installed. The checked-in Pixi workspace targets `osx-arm64`; no Linux
lockfile compatibility is implied.

Outputs are written under
`/home/runner/work/DataCenter/DataCenter/Presentation/scheduler_ramp_demo/`.
The synchronized case uses that directory; the irregular and busy cases use
its `irregular/` and `busy/` subdirectories. `--scenarios synchronized
--no-sensitivity` runs a smaller demonstration. `--no-plots` omits figures.

## Strategies

The default workload has nine independent batch jobs arriving in three waves.
Each Frontier node represents four GPUs. The jobs request 7,900 node
allocations in total (nodes can be reused), not 7,900 distinct physical nodes
or cohorts. In the synchronized case they require 1,752,000 useful node-seconds.
Every strategy preserves each cohort's requested duration and that same work.

| Strategy | Control action |
|---|---|
| Immediate activation | Starts all available work that fits, without intentional delay. |
| Fixed wave: 512 nodes/s | Starts at most 512 nodes of flexible work each second. |
| Fixed wave: 256 nodes/s | Starts at most 256 nodes each second. |
| Fixed wave: 226 nodes/s | Matched comparison for the reuse rule below. |
| Reuse + 226 new nodes/s | Replaces completing work immediately when useful work is queued, and permits up to 226 additional active nodes per second. |

The last rule converts 0.5 MW/s into 226 nodes/s using the calibration below.
It limits new load growth for flexible work, **not arbitrary load decreases**.
Completed work always stops consuming its active-power increment. There is no
dummy work or full-power holding to hide shutdowns. The fixed-wave policies
also reuse physical nodes, but count replacement starts against their admission
budget. Reuse can create extra overlap and need not outperform fixed waves.

The three scenarios are:

- **Synchronized:** equal durations within each wave, 1,600 background nodes.
- **Irregular:** unequal job durations, also 1,600 background nodes.
- **Busy:** synchronized arrivals with 6,800 background nodes, leaving only
  2,672 nodes for batch work; capacity queues arise even without smoothing.

The constant background represents service that must not be delayed. The
`flexible=False` job option also requires immediate whole-job admission or
raises an explicit capacity error. Flexible cohorts represent independent
batch tasks, not synchronized training workers or individual online inference
requests. Equal node-seconds are a work proxy, not measured AI productivity.

## Power and turbine coupling

The facility calculation uses the Frontier topology and component values in
`RAPS/config/frontier/`. The pinned calibration is:

- 9,472 available nodes after the missing rack is removed.
- 7.307 MW modeled facility idle power.
- 2.209 kW added when one node moves from CPU/GPU idle to full utilization.

These are fixed component-based screening values, not a dynamic cooling model
or a calibration to a modern production AI campus.

Three calculations are deliberately distinguished:

1. **Generator-only reference:** the unsupplemented facility demand drives
   the GGOV1 multishaft dynamic model. Load-frequency damping is disabled so
   falling frequency cannot silently reduce the requested compute load.
   Published trajectories stop at the first sampled crossing below 57.8 Hz;
   fuel/cost for an interrupted trajectory is not a valid full-study total.
2. **Ideal 10-second buffer:** a lossless, unlimited low-pass split estimates
   fast power and energy exchange. This retains the original demonstration
   for comparison; it is not an installed battery specification.
3. **Constrained coordinated operation:** actual storage power is bounded by
   its inverter, stored energy, efficiencies, and generator charging headroom.
   The remaining electrical demand is replayed through the turbine model.
   Mechanical power is a dynamic result, not assumed equal to an assigned
   generator target. Generator electrical load plus battery power equals
   facility demand at every dispatch interval.

The default hardware is two 22 MW turbine-base units, a 6 MW / 1.5 MWh battery,
10–90% charge bounds, initial charge of 50%, and 95% efficiency in each
direction. Generator headroom is 2 MW below the model's continuous ceiling;
this is not an N−1 reliability test. A final 120-second quiet recovery period
restores initial stored energy, so discharging the battery cannot appear as
free fuel savings. All schedules within a scenario use the same accounting
window, including at least 240 seconds after the latest job completion.

The same five power-management choices are tested for each schedule:
no storage action, and smoothing times of 2, 5, 10, and 20 seconds. Shorter
times follow demand faster; longer times ask storage to bridge more of the
change. Recovery uses the known study endpoint, not a forecast-free production
controller.

A candidate must satisfy the generator's 0.5 MW/s **one-second electrical
load-change** limit, headroom, 59.4–60.6 Hz sampled frequency band, mechanical
ceiling, and terminal energy recovery. Battery clipping is physical and
reported; it does not hide violations of those requirements. The dynamic
model still sees one-second steps, not continuously ramped loads.

Among feasible candidates, the study minimizes fleet fuel at an illustrative
$0.25/kg plus storage charge-and-discharge throughput at $20/MWh. These are
stated scenario assumptions, not market forecasts or a lifecycle-cost model.
Fuel quantities scale with fleet size; exhaust temperature does not.

Sensitivity cases use the same method with 2 MW / 0.5 MWh and 4 MW / 1 MWh
storage. Delay allowances of 0, 5, and 15 seconds apply to the **largest
additional full-start delay of any job relative to immediate admission in
the same scenario**. Capacity waiting in the busy baseline is reported
separately and is not erased by this definition. Hardware has no purchase
cost in the objective, so this comparison establishes feasibility and
operating tradeoffs, not an optimal installed battery size.

## Critical outputs

Each scenario's `data/strategy_summary.csv` reports ramps, useful work, facility
energy, job delay, completion extension, generator-only screening, ideal-buffer
estimates, and the best feasible coordinated operating result.

- `last_completion_s` is a timestamp; `makespan_s` starts at the first arrival.
- Unprefixed frequency/temperature columns describe **generator-only** runs;
  `best_` columns describe the chosen **coordinated** run.
- A missing best cost means no tested policy is feasible, not zero cost.
- A whole-trace p99 ramp can be zero when fewer than 1% of seconds change.
  Maximum and event-conditioned ramps are more useful here.
- Lower peak temperature slew does not imply lower temperature range,
  lower total cycling, or longer component life.

`dispatch_candidates.csv` retains every tested hardware/policy combination,
feasibility result, rejection reason, losses, and recovery error. Candidates
that fail simple limits can be rejected before running expensive dynamics.
`job_delays.csv` provides job-level details. Scheduler, generator-only, chosen
storage dispatch, and chosen coordinated turbine traces are saved as Parquet.
The dispatch row index is time in seconds; its final row is an endpoint,
not an additional energy interval.

The top-level data directory additionally contains `scenario_comparison.csv`,
`sensitivity.csv`, and `run_manifest.json` (arguments, hardware assumptions,
package versions, and source/configuration hashes). Only scenarios requested
by a run appear in those combined tables.

Figures show (1) starts and facility ramps, (2) generator-only response,
(3) scheduling and power tradeoffs, and (4) coordinated operation with the same
installed hardware. Torsion/fatigue helpers remain available for specialist
screening but are not run or promoted as a main result.

## Regenerated results

The completed run contains 15 schedule/scenario summaries, 225 tested
hardware/policy combinations, and 27 hardware/delay-allowance comparisons.

For the synchronized example, all schedules deliver 1,752,000 useful
node-seconds and consume 4.1765 MWh of facility energy over the same
1,030-second window:

| Rule | Maximum one-second ramp (MW/s) | Maximum added job delay (s) | Final completion extension (s) | Selected actual battery peak (MW) | Best tested operating cost ($) |
|---|---:|---:|---:|---:|---:|
| Immediate | 6.185 | 0 | 0 | 5.890 | 317.56 |
| 512-node wave | 1.131 | 5 | 4 | 4.379 | 316.79 |
| 256-node wave | 0.565 | 10 | 9 | 2.417 | 316.41 |
| 226-node wave | 0.499 | 12 | 10 | 0.000 | 316.02 |
| Reuse plus 226 | 1.498 | 12 | 3 | 4.121 | 316.98 |

The fixed-226 rule reduces maximum ramp by about **92%**, but does **not**
reduce facility energy or increase useful work. Its roughly **0.48%** modeled
cost reduction mostly reflects less storage use; fleet fuel decreases only
about **0.027%**. No battery action is needed for this particular case under
the study checks—not a claim that a real facility needs no storage.
The separate ideal ten-second buffer power estimate falls about 40%, while
its energy swing remains 17.18 kWh.

The matched reuse rule finishes sooner than the fixed wave, but has larger
power changes and requires more storage action. A more elaborate rule is
not automatically better.

- **Irregular:** 1,716,500 useful node-seconds and 4.2963 MWh in every schedule.
  Fixed-226 requires battery support despite its 0.499 MW/s facility ramp.
  Its selected frequency nadir is about 59.407 Hz, only 0.007 Hz above the
  study limit. Fixed-256 and fixed-226 costs are effectively tied.
- **Busy:** all strategies reach full capacity and have up to 240 seconds
  of baseline capacity waiting. Fixed-226 adds at most 11 seconds to any
  job's full-start wait, but does not extend the last completion. Facility
  energy remains 7.7728 MWh, and best tested cost falls from $433.29 to $431.79.
- **Smaller storage:** with 2 MW / 0.5 MWh or 4 MW / 1 MWh, none of the tested
  policies is feasible with at most five seconds of additional delay.
  At least one is feasible with fifteen seconds in every scenario. This
  establishes an operating tradeoff, not optimal battery purchasing.

The key finding is that modest scheduling flexibility changes what the
same power assets can support. It is not evidence of global optimality,
large fuel savings, or improved AI productivity.

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

## Implementation and verification log

The revised study addresses the original critique by matching admission
budgets, removing full-power holding, separating ideal from constrained storage,
replaying residual generator demand, correcting fleet fuel accounting, and
using common time windows. It adds irregular and capacity-constrained cases,
explicit delay allowances, finite-policy cost selection, reproducibility
metadata, and physical-invariant tests.

Existing scheduler tests cover calibration, node-second conservation, capacity,
protected work, matched admission, invalid inputs, constant traces, fleet
scaling, battery power/energy balance, efficiency losses, charge recovery,
saturation, infeasibility, protection crossing, and cost selection.

Completion checks also ensure that cached turbine trajectories are not reused
across different fleet sizes or sampling intervals, and that fuel accounting
includes the exact final time even when the output interval does not divide
the study duration. Temperature slew uses the actual interval lengths.

The existing test suite passes all 65 tests, and the existing smoke test passes.
No project dependency or lockfile was changed for the Linux validation run.
Repeating the irregular default-hardware case at a 0.025-second output
interval preserves every selected policy; selected frequency extrema differ
by less than 0.000006 Hz and costs by less than $0.000001. This is an
output-sampling check, not physical model validation.

The detailed novice-oriented explanation and results are in
`/home/runner/work/DataCenter/DataCenter/docs/scheduler_ramp_demo_report.tex`.
Build it with the repository's existing Tectonic dependency:

```bash
tectonic /home/runner/work/DataCenter/DataCenter/docs/scheduler_ramp_demo_report.tex
```

The report was also compiled with pdfLaTeX (two passes) in this environment:
Tectonic itself was available, but its default bundle host could not resolve.
Auxiliary build files are kept outside the repository.

## Limits and next research steps

1. Replay selected windows from `data/scheduler_data.csv` after validating its
   `time_start`, `time_end`, and `gres_alloc` schema.
2. Validate workload flexibility, whole-job admission, and service guarantees.
3. Extend the finite policy comparison to calibrated economic dispatch, battery
   lifetime/capital costs, unit commitment, and longer-horizon charge management.
4. Calibrate exhaust and component-metal thermal states against LM2500 data.
5. Evaluate fairness, starvation, prediction uncertainty, cooling, inverter
   response, sub-second behavior, and reliability over longer workload ensembles.
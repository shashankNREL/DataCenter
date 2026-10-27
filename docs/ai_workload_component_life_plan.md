# AI Workload Change and Power-System Component Life — Research Plan

Status: proposed plan. No code is authorized by this document.

Prepared: 2026-10-19.

Source reviewed: `bipartiteGraphs.txt` (GNN bipartite-matching proposal). That
document is not the basis of this plan. See §12 for the disposition.

---

## 1. The goal in one sentence

Measure how a change in the AI workload changes the life of the power-system
components that serve it.

The workload is the **input** that you change. The component damage is the
**output** that you measure. The power system is the **instrument** between
them.

## 2. Words used in this plan

Read this table first. The plan uses these words with one meaning only.

| Word | Meaning in this plan |
| --- | --- |
| Workload | The electrical demand that the computers make. |
| Trace | A record of power against time. |
| Knob | One property of the workload that you change on purpose. |
| Coherence | How much the computers change power together. |
| Lockstep | Full coherence. Every computer changes at the same moment. |
| Cadence | The repeat period of a training step. |
| Band | A range of speeds. |
| Ramp | The change of electrical power in one second. Unit: MW/s. |
| Trip | The turbine goes off line because the frequency is too low. |
| Damage meter | A calculation that converts a signal into damage. |
| Damage rate | Damage in one hour of operation. |
| Interface | The agreed input and output of a damage meter. |
| Surrogate | A fast table that replaces a slow calculation. |
| Response surface | A table or figure of damage against the knobs. |
| Resonance | Large response because the input period matches a natural period. |
| Register | The list of items that you invented. |
| Gate | A test. You continue only if the test passes. |

## 3. Why the design is inverted

An earlier plan held the workload fixed and changed the power system. This
plan holds the power system fixed and changes the workload.

Two things follow.

1. You need a **workload generator**. It must have knobs that mean something
   physical. This is the central new software.
2. You need a **damage-meter interface**. Two of the three meters do not exist
   yet. Define the interface now. Then a new meter plugs in later with no
   change to the harness.

> **Most important instruction in this plan.** Define the interface in §6
> before you write any meter. If you do not, you will build the harness three
> times.

## 4. What is ready, and what you must build

| Element | Status | Location |
| --- | --- | --- |
| Gas-turbine shaft torque, rainflow, Miner damage | **Ready** | `gas_plant/dynamics/torsional.py` |
| LM2500 fleet, GGOV1 multishaft governor | **Ready** | `gas_plant/fleet.py`, `gas_plant/dynamics/` |
| Facility power trace and ramp metrics | **Ready** | `tools/workload/scheduler_ramp_demo.py` |
| Coherence sweep, first version | **Ready** | `tools/presentation/plot_correlation_sweep.py` |
| BESS power split by low-pass filter | **Partial** | `tools/presentation/common.py:139` |
| BESS state of charge and degradation | **Build** | — |
| Transformer thermal, paper aging, oil condition | **Build** | plan in `docs/transformer_oil_life_modeling_plan.md` |
| Solar photovoltaic model | **Build** | absent from the repository |
| Grid tie and import limit | **Build** | absent from the repository |
| High-fidelity cooling (`raps.cooling`) | **Blocked** | see §11 |

The BESS split is a filter only. The project documentation records its limits:
it does not include state-of-charge management, inverter limits, or reserve
margin. An SOC study needs all three.

## 5. The three components work in three different bands

This table controls the whole plan. Read it before you choose a knob.

| Component | Damage driver | Time constant | Workload knob that moves it |
| --- | --- | --- | --- |
| Gas-turbine shaft | Torque cycle count and amplitude | 0.1 s to 10 s | Coherence, cadence, job scale |
| BESS state of charge | Energy throughput, cycle depth, C-rate | minutes to hours | Diurnal shape, burst length, idle fraction |
| Transformer oil and paper | Sustained hot-spot temperature | hours to seasons | Mean load, duty cycle, ambient temperature |

**Consequence.** One knob does not move all three components. A sub-second
change in the workload does not change the transformer oil life. The oil
temperature attenuates a 100 ms disturbance by four to five orders of
magnitude.

Predict this null result before you simulate it. Then report it. It answers
research question 2 in `docs/transformer_oil_life_modeling_plan.md`.

## 6. The damage-meter interface — define this first

Every damage meter takes the same input and gives the same output.

**Input**

| Field | Meaning |
| --- | --- |
| `t_s` | Time in seconds. A uniform grid. |
| `signal` | The driving signal for this meter. |
| `dt_s` | The time step. |
| `operable` | A boolean array. `False` where the plant has tripped. |
| `params` | The component parameters. |

**Output**

| Field | Meaning |
| --- | --- |
| `damage` | A number with no unit, or equivalent hours, or equivalent cycles. |
| `unit` | The name of the unit. |
| `rate` | Damage in one hour of operation. |
| `valid` | `False` if the signal left the validity range of the model. |
| `notes` | Text that states every assumption. |

**Three rules.**

1. A meter must return `valid = False` when the signal leaves its validity
   range. It must not return a number and stay silent. A past study reported
   "12,787 years to failure" from a trajectory after the turbine had tripped.
   The `valid` flag exists to stop this.
2. A meter must not report a lifetime in years. Report damage, or damage rate,
   or equivalent hours.
3. The harness must check `operable` before it calls any meter.

## 7. The workload knobs

These are the knobs that you change. Column 4 says whether your data can set
the value, or whether you must declare it.

| # | Knob | What it represents in AI work | Source of the value |
| --- | --- | --- | --- |
| W1 | Coherence, 0 to 1 | All-reduce barriers make GPUs swing together | **Declared.** See §8. |
| W2 | Step cadence, seconds | The repeat period of a training iteration | **Declared.** See §9. |
| W3 | Job scale, GPU count | One-GPU inference against many-GPU training | Partly measured |
| W4 | Train / infer mix | Training is sustained and periodic. Inference is bursty and diurnal. | Partly measured |
| W5 | Idle fraction | Checkpoint stalls, job failure, preemption | Measured |
| W6 | Facility scale | The multiplier from the sample to the facility | **Declared.** 11,345x today. |
| W7 | Diurnal shape | Day and night demand | Measured over 370 days |

Knobs W1, W2, and W6 are the ones that matter most, and all three are
declared, not measured. §8 and §9 say what to do about this.

## 8. Coherence — the dominant knob, and its evidence limit

Coherence is the most AI-specific property of the workload. It is also the
property that your data supports least.

Measured from `data/dcgm.csv`:

| Quantity | Value |
| --- | --- |
| Unique jobs with telemetry | 74,849 of 287,069 (26 per cent) |
| Unique nodes with telemetry | 225 |
| Jobs with 4 or more GPU rows | 1,337 |
| Jobs with 8 or more GPU rows | 1,043 |
| Largest single job | 64 GPU rows |
| Concurrent active GPUs, densest 2 h window | **6** |

`data/dcgm.csv` has **no timestamps**. It holds one summary row for each
(node, GPU, job). Therefore you cannot measure coherence against time from
this file. Use `data/nvidia_smi_first_1gb.parquet`, which has a measured
median per-GPU period of 0.106 s.

**Instruction.** Treat coherence as a declared design parameter. Sweep it.
State the range. Put the value on every figure. Do not present a
coherence-dependent number as a measurement of MIT SuperCloud.

Generalize the existing tool `tools/presentation/plot_correlation_sweep.py`.
It already sweeps coherence over 0, 0.25, 0.50, 0.75, and 1.00.

## 9. Cadence and resonance — the key risk to the headline

AI training repeats. One iteration is forward pass, backward pass, gradient
all-reduce, then repeat. This makes the power **periodic**, not random.

A periodic input can match a natural period of the plant. Two natural periods
matter here:

1. The torsional natural frequency of the shaft. Compute it from
   `gas_plant/dynamics/torsional.py`.
2. The governor oscillation mode. Compute it with `pixi run vv-smallsignal`.

If a training cadence lands near either frequency, the plant amplifies the
response.

> **Then damage is not monotonic in ramp rate.** A smaller oscillation at a
> resonant period can do more shaft damage than a larger oscillation at a
> non-resonant period.

**Consequence for your goal.** The rule "reduce the ramp rate to improve life"
can fail. The correct rule may be "avoid these frequency bands". Test this
before you report any ramp-based conclusion.

**Instruction.** Compute both natural frequencies first. Choose the cadence
sweep range to cover each frequency, one decade below it, and one decade above
it.

## 10. Plan of work

Each stage has a gate. If a gate fails, you stop and you report the result.

### Stage 0 — Natural frequencies and the sweep ranges (3 days)

1. Compute the shaft torsional natural frequency.
2. Compute the governor oscillation mode frequency.
3. Choose the cadence range from §9.
4. Choose the coherence range. Start with the five values already in use.
5. Write the trip threshold. Use 57.8 Hz.

**Gate 0.** Both frequencies are computed and written down.

### Stage 1 — The damage-meter interface and the first meter (1 week)

1. Write the interface from §6.
2. Wrap the existing shaft model behind it. Do not change the physics.
3. Confirm that the operability check runs before the damage calculation.
4. Write a hand-checked test case for the shaft meter.
5. Write two stub meters. Each stub returns `valid = False` and the text
   "not implemented".

**Gate 1.** The harness runs end to end with one real meter and two stubs.

> This gate is the point of the whole plan. After it passes, every later meter
> is an addition, not a rebuild.

### Stage 2 — The workload generator (2 weeks)

1. Build a generator that takes the knobs in §7 and gives a power trace.
2. Include a fast mode at 100 ms and a slow mode at 1 minute.
3. Verify the generator against the measured trace at coherence 1.0. The mean,
   the peak, and the p99 ramp must match the measured values.
4. Record the verification in a table.

**Gate 2.** At coherence 1.0 the generated trace matches the measured trace to
within 10 per cent on mean power, peak power, and p99 ramp.

### Stage 3 — Fast-band response surface (2 weeks)

1. Sweep coherence, cadence, job scale, and facility scale.
2. For each point, run 2 hours of 100 ms physics.
3. Record the operability result **first**.
4. Record the shaft damage rate second. Record the BESS throughput third.
5. Save a lookup table. Use the format of `data/gas_turbine_surrogate.csv`.
6. Draw the damage against cadence. Mark both natural frequencies on the
   figure.

**Gate 3.** The surrogate reproduces a held-out direct run to within 20 per
cent on the damage rate.

**Gate 3b.** You can state whether damage is monotonic in ramp rate, or
whether resonance breaks the relation. This is a required answer, not an
optional one.

### Stage 4 — The power mix and the energy balance (1 week)

1. Draw a one-line diagram. Show the grid tie, the turbines, the BESS, the
   photovoltaic array, the transformer, and the computers.
2. Write the rating and the source of every asset.
3. Write the dispatch order.
4. Give the BESS a fast duty for ramp and a slow duty for energy. State the
   split of the reserve.
5. Confirm that the energy balance closes at every step.

**Gate 4.** The energy balance error stays below 0.1 per cent over 24 hours.

### Stage 5 — The BESS state-of-charge meter (1 week)

1. Replace the low-pass split. Add the state of charge, the round-trip
   efficiency, the power limit, the energy limit, and the reserve band.
2. Reuse `rainflow_count()` from the shaft model on the state-of-charge trace.
   Both problems count cycles. One engine serves both.
3. Convert the cycle-depth histogram to capacity fade. Add calendar aging.
4. Report equivalent full cycles.
5. Plug the meter in behind the Stage 1 interface. Replace the stub.

**Gate 5.** The meter passes a hand-checked case. The harness needs no change.

### Stage 6 — The transformer meter (2 to 3 weeks)

1. Build the top-oil and hot-spot thermal model. Follow
   `docs/transformer_oil_life_modeling_plan.md`.
2. Separate the resistive, winding-eddy, and stray loss components. Do not
   apply one harmonic factor to all of them.
3. Add the Arrhenius paper-aging integral. Report equivalent aging hours.
4. Keep the oil-condition model separate from the paper-aging model.
5. Plug the meter in behind the Stage 1 interface. Replace the stub.

**Gate 6.** The meter passes a hand-checked case against a published example.

> This stage is the critical path of the project. It is modeling work, not
> scheduling work.

### Stage 7 — Slow-band annual study (1 week)

1. Clean `data/scheduler_data.csv`. Remove the 70,497 records with
   `nodes_alloc = 0`. Remove records with an invalid start time. Remove the
   placeholder duration of 31,999,976 s.
2. Build a 370-day load envelope at 1-minute steps.
3. Add an ambient temperature curve. State the location and the source.
4. Run the transformer meter and the BESS meter over the year.
5. Look up the shaft damage rate from the Stage 3 table at each hour.

**Gate 7.** The run completes in under 2 hours of wall-clock time. If it does
not, use 15-minute steps and repeat Gate 2.

### Stage 8 — The workload scenario matrix (2 weeks)

Hold the total computing work constant in every scenario. Change only the
shape of the workload.

| Scenario | W1 coherence | W2 cadence | W4 mix | W6 scale |
| --- | --- | --- | --- | --- |
| S1 Today, measured | 1.0 | measured | measured | 11,345x |
| S2 Independent jobs | 0.0 | measured | measured | 11,345x |
| S3 Large training, synchronous | 1.0 | at resonance | all training | 11,345x |
| S4 Large training, off resonance | 1.0 | off resonance | all training | 11,345x |
| S5 Inference fleet | 0.3 | none | all inference | 11,345x |
| S6 Mixed fleet | 0.6 | measured | half and half | 11,345x |
| S7 Growth | 1.0 | measured | measured | 22,690x |

For every scenario report six numbers.

1. p99 absolute ramp rate.
2. The time of the first trip, or "no trip".
3. The shaft damage ratio against S1.
4. The BESS equivalent full cycles.
5. The transformer equivalent aging hours.
6. The energy and the fuel used.

Then draw the figure. Put the shaft damage on one axis. Put the BESS fade on
the other axis. Draw the transformer aging as a contour.

**Gate 8.** No single scenario is best for all three components. If one is,
check the model. You have probably lost a coupling.

### Stage 9 — Sensitivity and reporting (1 week)

1. Vary the coherence across its full range. Report the result as a band, not
   a line.
2. Vary the transformer parameters across their uncertainty range.
3. Vary the ambient curve by plus and minus 5 degrees C.
4. State every conclusion as a ratio between scenarios.
5. Print the invented-item register in the report.

**Gate 9.** No conclusion depends only on coherence 1.0. If one does, label it
a bound, not a result.

## 11. The invented-item register

Write this list in every report. These items are **not** measured data.

| Item | Status |
| --- | --- |
| Coherence value | Declared. Only 6 concurrent GPUs exist in the dense window. |
| Facility scale multiplier, 11,345x | Declared. |
| Photovoltaic array and its output | Invented. No solar asset is in the data. |
| BESS and its state of charge | Invented. No battery is in the data. |
| Grid tie and the import limit | Invented. |
| Transformer and all its parameters | Invented. |
| Ambient temperature curve | Invented. |
| Harmonic spectrum | Invented. |
| Floorplan of racks and servers | Invented. Only 451 anonymous host names exist. |
| Per-server power against time | Invented. `dcgm.csv` holds summaries only. |
| Pairing of SuperCloud jobs with Frontier power constants | A deliberate choice. SuperCloud uses single Volta GPUs. The Frontier configuration uses four MI250X GPUs per node. |

Any conclusion about these items describes your assumption. It does not
describe MIT SuperCloud.

## 12. Items that are blocked, and the work-around

The high-fidelity cooling model in the vendored RAPS package does not run in
this checkout. This was verified, not assumed.

| Check | Result |
| --- | --- |
| `from raps.cooling import ThermoFluidsModel` | `ModuleNotFoundError: No module named 'uncertainties'` |
| `import fmpy` | Missing from the environment |
| `RAPS/config/frontier/cooling.json` points to | `models/Simulator_olcf5_base.fmu` |
| `RAPS/models/` exists | No |
| RAPS pinned versions | numpy 1.23.5, pandas 2.0.3, scipy 1.10.1 |
| This environment | numpy 2.5.1, pandas 3.0.3 |

**Work-around.**

1. Use `raps.power` only. That module imports correctly today.
2. Build a cooling surrogate. Use the method that already worked for the gas
   turbine. Sweep the ambient temperature and the IT power. Save a table of
   PUE. Read the table in the loop.
3. Import RAPS by path. Do not install it as a package. The pinned versions
   conflict with this environment.

## 13. Disposition of the source document

`bipartiteGraphs.txt` proposes a graph neural network for job-to-server
matching. Keep three ideas from it. Reject the rest for this goal.

| Idea | Disposition |
| --- | --- |
| Edge masking for hard constraints | **Keep.** It gives safety by construction and needs no learning. |
| Server-class aggregation | **Keep.** It shrinks the state. |
| Controller and plant split, with a surrogate for slow physics | **Keep.** It matches the existing gas-turbine method. |
| Graph neural network for scheduling | **Reject for this goal.** The workload is the input here, not the output. A learned scheduler is a different project. |
| Hungarian algorithm | **Reject.** It solves one-to-one assignment. The trace holds jobs of up to 435 nodes. Gang scheduling is set packing. |
| 1-minute control loop | **Reject.** The turbine trips at 9.2 s without a buffer. |
| The cited literature | **Verify before use.** Two citations could not be confirmed. The Decima description is not bipartite. The Zhang et al. description is a disjunctive graph, not a bipartite graph. |

## 14. Effort and the stopping points

| Stage | Effort | Can you stop here with a result? |
| --- | --- | --- |
| 0 Natural frequencies | 3 days | No |
| 1 Interface and first meter | 1 week | No |
| 2 Workload generator | 2 weeks | No |
| 3 Fast-band response surface | 2 weeks | **Yes.** The resonance finding stands alone. |
| 4 Power mix | 1 week | No |
| 5 BESS SOC meter | 1 week | **Yes.** |
| 6 Transformer meter | 2 to 3 weeks | **Yes.** The meter is a contribution by itself. |
| 7 Annual study | 1 week | **Yes.** The band-separation finding. |
| 8 Scenario matrix | 2 weeks | **Yes.** The main paper. |
| 9 Sensitivity | 1 week | Required for Stage 8 |

Total: about 13 to 14 weeks.

Stages 0 to 3 cost about 5 weeks and give the first publishable result. Start
there. Build the transformer meter in parallel if a second person is
available, because Stage 6 is the critical path.

## 15. How to state the conclusions

Use this form. Do not use absolute lifetimes.

> Good: "Scenario S3 raises the shaft damage rate by a factor of 4.2 against
> S1, at equal computing work. The coherence assumption is 1.0."

> Bad: "The shaft will fail in 12,787 years."

The second form was produced once in this project from a trajectory recorded
after the turbine had tripped. The `valid` flag in §6 and the operability gate
in §10 exist to prevent it.

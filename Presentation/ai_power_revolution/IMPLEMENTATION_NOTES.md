# AI Power Revolution Presentation Assets

This folder contains a rough slide deck and clean figures for a broad-audience presentation on powering AI data centers with behind-the-meter generation.

The deck is built around one plain-English idea:

> AI data centers do not only need enough energy. They also need power systems that can survive fast changes in demand.

## What Was Implemented

I added a new presentation asset folder:

- [Presentation/ai_power_revolution/slides.md](slides.md) - Markdown slide deck source.
- [Presentation/ai_power_revolution/slides.html](slides.html) - Browser-viewable slide deck with the same story and figures.
- [Presentation/ai_power_revolution/figures](figures) - Generated PNG figures for the deck.
- [Presentation/ai_power_revolution/data](data) - CSV tables behind the figures.

I also added reusable plotting scripts under [tools/presentation](../../tools/presentation). These scripts rebuild the presentation figures from repo data and documented study outputs.

No notebook cells were modified. The active notebook [notebooks/lm2500_ai_workload.ipynb](../../notebooks/lm2500_ai_workload.ipynb) was used as the source of the main AI workload and BESS story, but it was left unchanged.

## How To Regenerate Everything

From the repo root, run:

```bash
pixi run python tools/presentation/run_all.py
```

This regenerates all presentation figures and CSV metric tables.

If you only want one figure, run one of the individual scripts listed below.

## Plotting Scripts

| Script | Output figure | Output data | What it shows |
|---|---|---|---|
| [tools/presentation/plot_ai_workload_story.py](../../tools/presentation/plot_ai_workload_story.py) | [figures/01_ai_workload_signature.png](figures/01_ai_workload_signature.png) | [data/ai_workload_signature_summary.csv](data/ai_workload_signature_summary.csv) | The AI load trace, a zoom around the worst 100 ms ramp, and the ramp-rate histogram. |
| [tools/presentation/plot_frequency_trip_story.py](../../tools/presentation/plot_frequency_trip_story.py) | [figures/08_frequency_trip_story.png](figures/08_frequency_trip_story.png) | [data/frequency_trip_story.csv](data/frequency_trip_story.csv) | Full-window minimum frequency for one, two, three, and four unbuffered turbines plus one turbine with BESS. The first three cross the trip threshold; the last two ride through. |
| [tools/presentation/plot_bess_power_buffer.py](../../tools/presentation/plot_bess_power_buffer.py) | [figures/02_bess_power_buffer_timeseries.png](figures/02_bess_power_buffer_timeseries.png), [figures/03_bess_power_energy_sweep.png](figures/03_bess_power_energy_sweep.png) | [data/bess_power_buffer_sweep.csv](data/bess_power_buffer_sweep.csv) | The difference between a high-power, short-duration buffer and ordinary energy storage. |
| [tools/presentation/plot_correlation_sweep.py](../../tools/presentation/plot_correlation_sweep.py) | [figures/04_correlation_sweep.png](figures/04_correlation_sweep.png) | [data/correlation_sweep_metrics.csv](data/correlation_sweep_metrics.csv) | How synchronized AI jobs can create a harsher electrical load than decorrelated jobs with the same average demand. |
| [tools/presentation/plot_fleet_tradeoffs.py](../../tools/presentation/plot_fleet_tradeoffs.py) | [figures/05_fleet_tradeoffs.png](figures/05_fleet_tradeoffs.png) | [data/fleet_tradeoff_metrics.csv](data/fleet_tradeoff_metrics.csv) | The tradeoff between adding more turbines and adding a fast BESS buffer. |
| [tools/presentation/plot_islanding_resiliency.py](../../tools/presentation/plot_islanding_resiliency.py) | [figures/06_islanding_resiliency_summary.png](figures/06_islanding_resiliency_summary.png) | [data/islanding_resiliency_metrics.csv](data/islanding_resiliency_metrics.csv) | Islanding, resynchronization, fuel reserve, voltage, and CO2 discussion metrics. |
| [tools/presentation/plot_architecture_tradeoff_map.py](../../tools/presentation/plot_architecture_tradeoff_map.py) | [figures/07_architecture_tradeoff_map.png](figures/07_architecture_tradeoff_map.png) | [data/architecture_tradeoff_scores.csv](data/architecture_tradeoff_scores.csv) | A qualitative comparison of grid-only, gas-only, gas+BESS, fleet-only, and hybrid designs. |
| [tools/presentation/plot_scheduler_scenarios.py](../../tools/presentation/plot_scheduler_scenarios.py) | [figures/10_scheduler_scenario_rulers.png](figures/10_scheduler_scenario_rulers.png) | [data/scheduler_scenario_timeline.csv](data/scheduler_scenario_timeline.csv), [data/scheduler_scenario_results.csv](data/scheduler_scenario_results.csv) | Node-use rulers and optimized results for the synchronized, irregular, and busy scheduler scenarios. |
| [tools/presentation/run_all.py](../../tools/presentation/run_all.py) | All figures | All CSVs | Runs every script above in sequence. |

The shared helper file [tools/presentation/common.py](../../tools/presentation/common.py) contains common functions for rebuilding the SuperCloud trace, calculating BESS metrics, setting plot style, and writing outputs.

## Where The Numbers Come From

The rough deck combines two kinds of information.

First, it uses computed results from this repo:

- The corrected MIT SuperCloud AI workload trace and BESS split from [notebooks/lm2500_ai_workload.ipynb](../../notebooks/lm2500_ai_workload.ipynb).
- The review and interpretation in [docs/ai_workload_review.md](../../docs/ai_workload_review.md).
- The fleet scaling study in [tools/vv/fleet_study.py](../../tools/vv/fleet_study.py).
- The ANDES islanding and resiliency examples in [examples/example_islanding.py](../../examples/example_islanding.py) and [examples/example_resiliency.py](../../examples/example_resiliency.py).

Second, it uses presentation-grade summary figures to make the ideas easier for a broad audience.

These summary figures are not meant to replace detailed engineering studies. They are meant to help people see the problem clearly.

## Novice Explanation Of The Main Ideas

### 1. Peak MW Is Not The Whole Problem

Megawatts, or MW, tell us how much power is being used at an instant.

Most people ask whether a power plant has enough MW to serve a data center. That is important, but incomplete.

The harder question is how quickly the demand changes. If the data center jumps by many MW in a fraction of a second, the turbine may not be able to react fast enough. During that delay, the spinning generator slows down or speeds up, and system frequency moves away from 60 Hz.

If frequency moves too far, protection systems trip the equipment offline. The site can fail even if the turbine was large enough on paper.

### 2. A Power Buffer Is Different From Energy Storage

A battery used for energy storage is like a large tank. It is meant to supply energy for a long time, often 1 to 4 hours.

A battery used as a power buffer is more like a shock absorber. It supplies or absorbs a lot of power for a short time.

In the AI workload case, the battery may need many MW but only a small number of kWh. That means it is not mainly there to run the data center for hours. It is there to smooth the fast mismatch between the AI load and the gas turbine response.

Plain-English comparison:

- MW is how wide the pipe is.
- MWh is how big the tank is.
- AI fast ramps may need a wide pipe more than a huge tank.

### 3. Not All Batteries Are The Same

Every battery system has two separate size limits:

- **Power (MW)** is burst strength: how much power the battery and inverter can push or absorb at one instant. This is the width of the pipe.
- **Energy (MWh)** is endurance: how long the battery can keep doing it. This is the size of the tank.

The modeled 10-second smoothing case needs about 9.9 MW of power but only 0.0184 MWh, or 18.4 kWh, of usable energy swing. Dividing energy by power gives about 6.7 seconds at full output. It is therefore a very wide pipe attached to a very small tank.

That is different from a common 10 MW / 40 MWh four-hour utility battery. The utility battery is designed to move energy over hours; this AI buffer is intended to catch sharp changes for seconds while the turbine catches up.

The common shorthand is C-rate:

```text
C-rate = MW / MWh
```

A 100 MW / 400 MWh battery is a 4-hour battery, or 0.25C.

A 100 MW / 100 MWh battery is a 1-hour battery, or 1C.

For the modeled AI buffer, 9.9 MW / 0.0184 MWh is about 540C equivalent. That number is a comparison showing how power-heavy the service is. It is **not** a recommendation to run an ordinary utility LFP battery cell at 540C. A real design must separately check cell chemistry, inverter power, bus and cable limits, cooling, state-of-charge headroom, cycle life, safety margin, and controls. The practical solution may use a purpose-built high-power battery, UPS, flywheel, supercapacitor, or a hybrid.

The four panels on the slide say:

- **Upper left:** changing how slowly the turbine is allowed to respond does not remove the roughly 8-10 MW burst requirement.
- **Upper right:** asking the battery to cover the mismatch longer increases the required stored energy.
- **Lower left:** the modeled cases sit in a high-power, low-energy region.
- **Lower right:** the 10-second AI buffer is far more power-intensive than ordinary one-to-four-hour storage.

### Frequency Comparison Update

The five-case frequency slide uses the same full two-hour AI trace for each unbuffered fleet case. The computed minima are 20.730 Hz for one turbine, 27.961 Hz for two, 20.000 Hz for three, and 59.527 Hz for four. The corrected notebook supplies 59.800 Hz for one turbine with BESS.

The one-, two-, and three-turbine cases cross the 57.8 Hz protection threshold. Their later extrema are model extrapolations, because a real generator would disconnect at the first trip crossing. The non-monotonic two-versus-three-turbine minima therefore should not be interpreted as three turbines being physically worse than two after a trip; both configurations fail the ride-through test.

### 4. Synchronization Matters

If many jobs run independently, their power changes partly cancel out.

If a large training job makes many GPUs change power at the same time, the data center can behave like one giant synchronized electrical load.

That is why the deck asks whether the real problem is energy, peak power, or synchronization.

### 5. More Turbines Help, But They Are Not Free

Adding more turbines gives more spinning mass and more total ramp capability. That helps the system survive fast load changes.

But if the data center load is modest compared with the total installed gas capacity, each turbine may run at low load. Gas turbines are usually less efficient at low load, so the site can burn more fuel and emit more CO2 for the same useful electricity.

That is the fleet tradeoff: more machines can improve dynamics but worsen fuel and maintenance economics.

### 6. Islanding And Resynchronization Are Different Problems

Islanding means the data center separates from the grid and runs on local generation.

Resynchronization means reconnecting to the grid.

Those are not the same problem. A site can survive islanding but still reconnect badly if voltage, frequency, and phase angle are not matched.

This matters for behind-the-meter generation because private equipment can still create public-grid consequences when it reconnects.

### 7. Software Can Help The Power System

AI jobs are controlled by software schedulers. That means some power behavior can also be shaped in software.

A scheduler could gradually start jobs, stagger checkpoints, or delay non-urgent work during grid stress. This will not replace electrical hardware, but it can reduce the size and cost of that hardware.

This is one of the most important discussion points for the presentation: AI workload scheduling may become part of energy-system design.

### 8. The Three Scheduler Scenarios

A node is one modeled server with four GPUs. Nine flexible jobs arrive in three groups. The selected rule starts at most 226 nodes each second instead of turning on every requested node at once. No useful work is removed; a delayed cohort runs for its full requested duration and finishes correspondingly later.

The ruler figure uses a triangle for each job request. A pale bar means only some of that job's requested nodes are active, while a dark bar means all requested nodes are active. Bars that overlap horizontally represent jobs using different nodes at the same time. A gray line marks waiting that would occur even with immediate scheduling because the cluster lacks free nodes.

- **Synchronized:** jobs in each arrival wave have equal durations. Their completions occur close together, and replacement work is available at convenient times. The fixed 226-node/s rule reduces the worst ramp from 6.18 to 0.50 MW/s. The selected operating point needs no battery action and the last job finishes 10 seconds later.
- **Irregular:** the same job sizes arrive at the same times, but their durations differ. Completions are scattered, producing several closely spaced power changes. The ramp still falls to 0.50 MW/s, but the best tested operating point needs 2.22 MW of battery support and has a narrow 59.407 Hz minimum-frequency margin. The last job finishes 7 seconds later.
- **Busy:** 6,800 of 9,472 nodes are already occupied, leaving only 2,672 for new jobs. Two jobs wait as long as 240 seconds because the cluster is full even under immediate scheduling. The smoothing rule adds at most 11 seconds beyond that unavoidable wait, reduces the ramp from 5.90 to 0.50 MW/s, selects no battery action, and does not delay the final completion.

Under the report's 15-second extra-delay allowance, the fixed 226-node/s rule is the best tested schedule in all three scenarios. This is a comparison among tested policies, not proof of a globally optimal scheduler. The reported costs are illustrative short-window operating costs and exclude hardware purchase and the business value of job delay.

## First Pass Completed

The first pass in the execution plan was to reuse and repackage existing repo outputs.

Completed items:

- Used the existing AI workload notebook story without modifying the notebook.
- Used the existing fleet-study results as the basis for the fleet tradeoff figure.
- Used the existing ANDES islanding/resiliency examples as the basis for the islanding summary.
- Generated a rough slide deck in Markdown and HTML.
- Generated clean PNG figures for the main presentation story.

## Second Pass Completed

The second pass was to add lightweight extensions for presentation-level analysis.

Completed items:

- Added a BESS power/energy sweep across smoothing time constants.
- Added explicit equivalent duration and C-rate metrics for the BESS story.
- Added a correlation/coherence sweep to show how synchronized AI jobs make power-system stress worse.
- Added an architecture tradeoff map to prompt discussion across grid, gas, BESS, fleets, and workload APIs.
- Wrote each figure's source data to CSV so labels, legends, and styling can be changed later without reverse-engineering the plot.

## Important Caveats

The deck is useful for discussion, not final design.

The exact values depend on assumptions that should be stated clearly:

- The MIT SuperCloud trace is scaled up from a smaller GPU sample.
- The fully synchronized case is a worst-case bound, not necessarily the expected behavior of every data center.
- The correlation sweep is a presentation-grade synthetic illustration, not a validated facility-scale workload model.
- The LM2500 dynamics and torsional calculations are screening-level models.
- The fleet tradeoff figure summarizes documented study results and should be rerun with any final assumptions before publication.
- The architecture tradeoff map is qualitative. Its scores are prompts for debate, not optimization outputs.

## Suggested Next Improvements

For a more polished or technical version, the most valuable next steps are:

1. Add a real job-level bootstrap trace generator using more SuperCloud data.
2. Add a BESS model with state of charge limits, inverter MW limits, and efficiency.
3. Move the high-level ANDES scenario runner to the newer v2 case builder.
4. Add proper resynchronization logic with frequency, voltage, and phase checks.
5. Add a small cost model for turbine capex, BESS MW cost, BESS MWh cost, fuel, CO2, and maintenance.
6. Connect RAPS scheduling outputs to the power-system analysis so software-side load shaping can be compared with hardware-side buffering.

## Files Added

Presentation assets:

- [Presentation/ai_power_revolution/slides.md](slides.md)
- [Presentation/ai_power_revolution/slides.html](slides.html)
- [Presentation/ai_power_revolution/IMPLEMENTATION_NOTES.md](IMPLEMENTATION_NOTES.md)

Plotting code:

- [tools/presentation/common.py](../../tools/presentation/common.py)
- [tools/presentation/plot_ai_workload_story.py](../../tools/presentation/plot_ai_workload_story.py)
- [tools/presentation/plot_frequency_trip_story.py](../../tools/presentation/plot_frequency_trip_story.py)
- [tools/presentation/plot_bess_power_buffer.py](../../tools/presentation/plot_bess_power_buffer.py)
- [tools/presentation/plot_correlation_sweep.py](../../tools/presentation/plot_correlation_sweep.py)
- [tools/presentation/plot_fleet_tradeoffs.py](../../tools/presentation/plot_fleet_tradeoffs.py)
- [tools/presentation/plot_islanding_resiliency.py](../../tools/presentation/plot_islanding_resiliency.py)
- [tools/presentation/plot_architecture_tradeoff_map.py](../../tools/presentation/plot_architecture_tradeoff_map.py)
- [tools/presentation/run_all.py](../../tools/presentation/run_all.py)

Generated figures and data:

- [Presentation/ai_power_revolution/figures](figures)
- [Presentation/ai_power_revolution/data](data)

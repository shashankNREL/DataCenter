# Benefits of coupling gas_plant with ANDES

ANDES (open-source Python power-system dynamic simulator from CURENT, Texas A&M) is what `gas_plant` isn't: multi-bus electrical networks, synchronous-machine dynamics, AVR / PSS / governor models, transient stability, small-signal eigenvalue analysis. Coupling them gives you the *plant economics & fuel/CO2 side* (`gas_plant`) **plus** the *electrical-network dynamics* (ANDES) in one workflow.

This document catalogs what becomes possible. See `electrical_transient_modeling.md` for the broader context (Path A / B / C tradeoffs).

## 1. Frequency dynamics — the core motivation

| Study | Concrete question |
|---|---|
| N-1 contingency | If our largest unit trips, what's the frequency nadir and ROCOF? Does any unit hit underfrequency-relay limits? |
| Load step response | A 200 MW load comes on instantly — how do the gas units share the response under their droop settings? |
| Inertia adequacy | At 60 % renewable penetration, what synchronous inertia (in MW·s) do we need from gas to keep ROCOF under 1 Hz/s? |
| AGC tuning | What secondary-control gain restores 60 Hz in <10 min without overshooting? |
| Fast frequency response | If our gas fleet has 30 s primary response, how much does adding battery FFR shrink the nadir? |

## 2. Transient stability

| Study | Concrete question |
|---|---|
| Critical clearing time | How long can a 3-phase fault on the data-center feeder persist before our generators lose synchronism? |
| First-swing | Does the gas plant remain stable after a nearby fault is cleared in 100 ms? |
| Cascading failures | If line X trips, do any units overload to relay limits? |

## 3. Small-signal stability (eigenvalue analysis)

ANDES gives Jacobian-based eigenvalues at any operating point.

- Are there poorly damped inter-area modes in this fleet configuration?
- Does adding a PSS on unit #3 improve damping enough to allow tighter dispatch?
- How does damping change as we shift dispatch from CCPP-heavy to GT-heavy?

## 4. Islanded / microgrid operation

| Study | Concrete question |
|---|---|
| Intentional islanding | If the grid tie opens, can our local gas + load island survive? At what stability margin? |
| Microgrid composition | Gas + battery + PV serving a 50 MW data center — what's the minimum gas spinning to keep frequency in a 1 % band? |
| Re-sync | What's the sync window (Δf, Δφ, ΔV) for closing back to grid after island? |
| Black-start cascade | Sequence: aux power → first GT sync → pick up data-center hotel load → bring up second GT → ramp to full |

## 5. Data-center–specific scenarios (most relevant for this project)

The coupling is most differentiated here — ANDES alone doesn't model fuel costs, `gas_plant` alone doesn't model electrical dynamics, but the data-center use case needs both.

| Study | Concrete question |
|---|---|
| Behind-the-meter generation sizing | 200 MW data center with on-site gas + grid tie — what's the right gas capacity given grid-outage frequency, fuel cost, CO2 target? |
| Resiliency | If the grid drops for 4 hours, can on-site gas + UPS carry the data-center load without violating any IT-side reliability target? |
| Grid services from backup generation | $/MW·yr the data center can earn by offering its gas backup to frequency regulation — at what fuel cost / wear penalty? |
| Demand response | Our data center can curtail 20 MW in 30 s — how does that change the gas fleet's primary-response burden? |
| Co-located gas + storage + PV | Find the mix (gas / battery / PV) that minimizes cost subject to (a) frequency stability post-islanding, (b) CO2 cap, (c) reliability target. |
| Hybrid dispatch policy | Run gas at high efficiency point and let battery shave variability, or run gas at part load for spinning reserve? Cost / CO2 difference over a year? |

## 6. Renewable integration

| Study | Concrete question |
|---|---|
| Replacement of baseload | As we add 500 MW solar, how does that change our gas fleet's duty cycle, capacity factor, CO2 per MWh? |
| Inertia replacement | If we displace 2 GW of synchronous inertia with grid-forming inverters, how much gas needs to stay online for stability? |
| Curtailment vs. ramp-down | Cheaper to curtail solar or ramp gas down? Depends on fuel cost, CO2 price, ramp wear. |

## 7. Economic + dynamic co-optimization

ANDES doesn't natively do production cost or unit commitment. With `gas_plant` providing the fuel/CO2 layer, you can wrap an optimization loop around dynamic-feasibility checks:

- Find the dispatch that minimizes fuel + CO2 cost, subject to primary-reserve adequacy verified by an ANDES transient run.
- Co-optimize energy + spinning-reserve markets, where reserve cost = additional fuel burn at part load (`gas_plant`) and reserve quantity = stable response verified by ANDES.
- Annual production cost simulation with hourly dispatch (`gas_plant`) + sub-hourly stability checks at high-risk hours (ANDES).

## What this coupling does NOT enable

Honest limits, so you pick the right tool for the right question:

- **Electromagnetic transients** (sub-cycle, voltage ride-through compliance, inverter control loops faster than ~50 Hz). Need EMT tools (PSCAD, EMTP, OpenIPSL). ANDES is RMS / phasor-domain.
- **Plant-internal control** below the governor (combustion controller, fuel-valve dynamics, NOx trims, HRSG drum-level control). `gas_plant` flattens this into the steady-state surrogate. For these, go back to a ThermoPower transient simulation (Path C).
- **Steam-side dynamics during transients** (CCPP startup HRSG warming, ST thermal stress). Same — needs ThermoPower transient or a dedicated thermal model.
- **Detailed distribution network** (LV feeders, kV-side voltage regulation). ANDES does transmission well; for distribution add OpenDSS or PowerModelsDistribution.
- **Market-clearing engines** with security-constrained UC / ED. `gas_plant` gives fuel/CO2 cost curves; ANDES doesn't do market clearing. Add Pyomo or another optimization layer.

## What it takes to actually do the coupling

Roughly the shape of one of the earlier phases — concrete and finite:

1. **Wire `gas_plant` as a turbine-governor block in ANDES.** ANDES has an SDK for custom models; or simpler, use the built-in `TGOV1` and parameterize from `gas_plant`. The surrogate becomes the steady-state characteristic table inside a standard governor.
2. **Provide reasonable defaults** for the machine-side parameters ANDES needs and `gas_plant` doesn't carry: inertia H (typical 4–6 s for gas plants), subtransient reactances, governor droop / time constants. Standard textbook values; expose as overrides.
3. **Build 2–3 reference cases** (single-machine-infinite-bus, two-area, IEEE 9-bus or 14-bus) with gas units instantiated from the surrogate.
4. **Add post-processing** that joins ANDES trajectories with `gas_plant` fuel/CO2 to give cost-and-emissions over the simulated period.

Estimated effort: comparable to one of the existing phases (~half a day per piece, ~2–3 days total for a working integration).

## Recommended first deliverable

Single-bus dynamic test case: **one gas unit + one data-center load, demonstrating islanding survival and resync.** Proves the integration works on a problem you actually care about, and unlocks every §5 study from there.

That's what Phase 6 builds. See `phase6_setup.md` for the actual implementation and example runs.

## Highest-value studies for the DataCenter project

If you can only do a few of these, prioritize:

1. **§5 Resiliency** — most direct value for data-center planning.
2. **§4 Islanding survival** — prerequisite physics for §5.
3. **§1 Frequency dynamics** — foundation for everything else.
4. **§7 Economic + dynamic co-optimization** — long-term, where the coupled tool really differentiates from either alone.

The transient-stability and small-signal studies (§2, §3) are useful but conventional — most utilities already do them with PSS/E or PowerWorld.

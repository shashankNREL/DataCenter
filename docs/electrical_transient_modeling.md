# Modeling electrical transient behavior with gas_plant

How to add frequency dynamics, islanded operation, and blackstart to the steady-state `gas_plant` surrogate built in Phases 1–5.

## Why the current package can't do this directly

`gas_plant` is a steady-state dispatch surrogate. Every call is algebraic — no time evolution, no rotor inertia, no governor, no thermal lag. That's by design: it answers "what does the plant produce at operating point X" in milliseconds, which is what a larger dispatch tool needs in the hot path.

| Phenomenon | Physics needed | In `gas_plant` today? |
|---|---|---|
| Frequency fluctuations | Swing equation `(2H/ωs) dω/dt = P_mech − P_elec`, governor droop, AGC | No — no rotor state, no governor |
| Islanded operation | Swing equation + isochronous governor + frequency-dependent load | No |
| Blackstart | Sequenced state machine (aux power → cold roll → ignition → ramp → sync → load), HRSG warming | No — no time-of-startup, no thermal lag |

## ThermoPower primitives that could be used

The vendored library has the building blocks if you go the high-fidelity route:

- `ThermoPower.Electrical.Generator` — has the swing equation. Inertia computed from `Pnom·Ta/ωm²`. This is the model whose `J` is `final` (the bug that broke CCPP_Sim3), but the model itself is fine when used directly without `GeneratorGroup`.
- `ThermoPower.Electrical.NetworkGrid_Pmax` — grid coupling with breaker (open ⇒ islanded).
- `ThermoPower.Electrical.Grid` — large grid with primary frequency control (droop).
- `ThermoPower.Electrical.PowerSensor`, `FrequencySensor` — measurements.
- `OldSwingEquation.Generator_SE` — alternative swing-equation generator without the `final J` issue (workaround if the bug isn't patched).
- No GT governor model out of the box — would need to add a standard PID-with-droop.

## Three implementation paths, in increasing fidelity / effort

### Path A — Pure-Python dynamic layer on top of the surrogate

Lowest effort. Best fit for the "robust + simple to integrate" preference. Single-bus, frequency-domain studies (seconds to minutes).

Modules to add (kept separate from `gas_plant/` to preserve standalone surrogate usage):

1. `prime_mover.py` — first-order fuel-response time constant on top of `GasTurbinePlant` (typical GT τ ≈ 3–10 s from setpoint change to mechanical power).
2. `swing.py` — 2nd-order rotor model with state `[ω, δ]`, derivative `(P_mech − P_elec)/M − D·Δω`. Use `scipy.integrate.solve_ivp` for adaptive RK45.
3. `governor.py` — two modes:
   - `DroopGovernor(droop=0.05)`: `P_setpoint = P0 − (1/droop) × (f − fnom)`.
   - `IsochronousGovernor(Ki)`: integrates frequency error to zero — single-unit-island behavior.
4. `DynamicPlant(unit, governor, inertia_time_Ta=10, damping=0.01)` class with:
   ```python
   plant.step(dt, frequency_grid, load_setpoint)   # one timestep
   plant.simulate(scenario)                        # full trajectory
   plant.synchronize_to_grid()                     # close breaker
   plant.island()                                  # open breaker
   ```

Scenarios this unlocks:
- Load step / frequency event: drop 10 % of generation, watch unit ramp under droop, observe Δf nadir.
- AGC response: secondary control adjusts P0 over minutes.
- Loss-of-largest-unit: frequency excursion across a small fleet.

Cannot capture: voltage dynamics, electrical transients faster than ~100 ms, sub-cycle phenomena, machine-electromagnetic dynamics. Fine for frequency-time-scale studies, not transient stability / short-circuit analysis.

### Path B — Couple to a dedicated power-systems library (ANDES)

Hand off electrical dynamics to a tool that already does them well; keep `gas_plant` as the prime-mover model. ANDES is open-source Python, conda-forge-installable, with synchronous machine + governor + exciter + PSS templates and built-in transient-stability + small-signal analysis. **This is what Phase 6 implements.** See `andes_coupling_benefits.md` for the studies it enables.

Outline (now implemented):
1. New package `gas_plant_andes/` — imports `gas_plant`, but `gas_plant` does NOT import it. Standalone surrogate usage is preserved.
2. `case_builder.py` builds ANDES cases parameterized from `GasTurbinePlant` / `CombinedCyclePlant` configs (rated power, fuel curve) plus standard textbook defaults for machine-side parameters (H, Xd, Xq, governor droop).
3. `scenarios.py` packages common scenarios (islanding, resync, frequency event, regulation signal).

This is the right path if the larger tool needs multi-bus dynamics, contingencies, fault studies, or anything involving the network around the plant. Overkill if you only need single-bus / single-fleet response — in that case Path A is enough.

### Path C — Full ThermoPower transient simulation

Build a `.mo` model that wires `GasTurbineSimplified` to `Electrical.Generator` to `NetworkGrid_Pmax` with a custom governor PID, simulate in OpenModelica via the existing Docker toolchain, post-process trajectories in Python.

Outline:
1. Write `tools/dynamic/GTGenerator.mo` with:
   - `GasTurbineSimplified` as the prime mover
   - `Electrical.Generator(Pnom=235e6, Ta=10)` — swing equation, no `GeneratorGroup` wrapper, so no `J=J_shaft` bug
   - PID governor: input `(fnom − f_measured)`, output GTLoad setpoint with rate limit
   - `NetworkGrid_Pmax` with `hasBreaker=true`
2. Three test scenarios:
   - `TestFrequencyEvent.mo` — grid frequency drop
   - `TestIsland.mo` — open breaker mid-run
   - `TestBlackstart.mo` — sequenced startup (using `Modelica.StateGraph`)
3. Sweep / parameterize in `.mos` scripts (same workflow as the surrogate sweep); dump time-series CSV; load into Python for plotting.

Highest physics fidelity. Slowest iteration cycle (compile + transient per scenario). Necessary if you want the steam-side dynamics of CCPP startup or detailed plant-internal control behavior.

## Blackstart specifically

Blackstart is different in character from the other two — it's a **scheduled sequence**, not a continuous response. Typical sequence for a gas plant:

```
t=0       Aux power online (diesel/battery)
t=2-5min  GT cold roll on starting motor
t=5-7min  Ignition, light off
t=7-15min Acceleration to nominal speed (isochronous governor)
t=15min   Excite field, energize busbar
t=15-30m  Pick up first block load (hotel load, then critical systems)
t=30-60m  HRSG warming (CCPP only)
t=60-90m  Steam turbine sync (CCPP only)
t=90m+    Full load capability
```

For dispatch tools this is usually modeled as a **state machine + look-up of (time-since-start) → (available capacity)**. No simulation needed.

### Outline (Path A-style, low effort)

1. `blackstart.py` with a `BlackstartProfile` class — given plant type (GT or CCPP), returns `available_capacity_pu(t)` from t=0 onward, plus boolean `can_carry_load(t)`.
2. Tunable parameters: `time_to_first_sync`, `time_to_full_load`, ramp rates per phase.
3. Defaults from generic industry numbers; user can override per-plant.
4. Integrates cleanly with the larger tool's outage / restoration scheduler.

For higher fidelity on the HRSG-warming step of CCPP blackstart, Path C (ThermoPower transient HRSG model) is the right tool. But for dispatch-tool blackstart, the state-machine is enough.

## Sequencing recommendation

For the DataCenter project priorities (robust + integration-friendly):

1. **Path B (ANDES coupling) first** for frequency and island dynamics — proven library, open source, Python-native, fits the rest of the stack.
2. **State-machine blackstart** (Path A flavor) layered on top — cheap to add, gives the larger tool the time-vs-capability profile it needs for outage scheduling.
3. **Path C only if** the larger tool grows into detailed CCPP startup studies or plant-internal control work.

Phase 6 implements (1) and provides hooks for (2).

## Time-scale guide

When in doubt about which path is right for a question:

| Time scale | Best path | Example questions |
|---|---|---|
| sub-cycle (<20 ms) | EMT tool (PSCAD, EMTP) — not covered here | Inverter control, short-circuit, lightning |
| 100 ms – seconds | Path B (ANDES) | Transient stability, fault clearing, first-swing |
| Seconds – minutes | Path A or B | Frequency response, governor tuning, AGC |
| Minutes – hours | Path A + state machine | Blackstart, ramp scheduling, outage recovery |
| Hours – years | `gas_plant` alone | Dispatch, capacity planning, fuel + CO2 accounting |

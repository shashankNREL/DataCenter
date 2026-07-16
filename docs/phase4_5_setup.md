# Phases 4–5 — combined-cycle plant + packaging

Operational record of adding the CCPP surrogate and packaging the runtime component. Run 2026-05-21 against the toolchain established in Phases 1–3.

## Phase 4: CombinedCyclePlant

### 4.1  Why analytical, not CCPP_Sim3

Attempted to use `ThermoPower.PowerPlants.Simulators.CCPP_Sim3` directly. `checkModel` failed:

```bash
docker run --rm -v "$PWD:/work" -w /work \
  -e OPENMODELICALIBRARY=/work/tools/build_surrogate/libs \
  openmodelica/openmodelica:v1.26.7-minimal \
  omc /work/tools/build_surrogate/check_ccpp.mos
```

```
=== checkModel CCPP_Sim3 ===

[/work/ThermoPower/ThermoPower/PowerPlants.mo:17812:11-17812:20:writable] Notification: From here:
[/work/ThermoPower/ThermoPower/package.mo:145:5-145:107:writable] Error: Trying to override final element J with modifier '= J_shaft'.
```

Root cause: `ThermoPower.Electrical.Generator` declares `J` as `final` (`package.mo:145`, computed from `Pnom*Ta/omega_m_nom^2`), but `GeneratorGroup` (`PowerPlants.mo:17812`) attempts `J = J_shaft`. This is a latent bug in ThermoPower 3.1 that affects every Simulator using `GeneratorGroup`, including CCPP_Sim3.

Decision: build the CCPP surrogate analytically over the already-validated simple-cycle GT data. Reasons:

1. Patching vendored code creates ongoing maintenance pain.
2. Even if patched, CCPP_Sim3 is a complex transient model — sweeping it across off-design loads is fragile.
3. Industry dispatch / planning tools routinely use HRSG + bottoming-cycle energy-balance models on top of topping-cycle physics. Same physics, ~1-2% accuracy vs. detailed models for steady-state queries, far more robust.

### 4.2  Analytical model

For each load setpoint, given the GT surrogate outputs `P_GT(L)`, `m_exh(L)`, `T_exh(L)`, `fuel(L)`:

```
Q_HRSG       = m_exh × cp_gas × max(T_exh − T_stack, 0)
η_bottoming  = η_bot_nom × (T_exh − T_stack) / (T_exh_nom − T_stack)    [Carnot-style derate]
P_ST         = η_bottoming × Q_HRSG
P_total      = P_GT + P_ST
fuel_CCPP    = fuel_GT     (no supplementary firing in CCPP_Sim3 either)
CO2_CCPP     = CO2_GT
exhaust_T    = T_stack     (post-HRSG, emitted to atmosphere)
```

Defaults (matching ThermoPower's CCPP_Sim3 / HRSG_3LRh design point):

| Parameter | Default | Source |
|-----------|---------|--------|
| `eta_bottoming_nominal` | 0.32 | typical for 3-pressure-level reheat HRSG |
| `T_stack_K` | 363.0 | CCPP_Sim3 `sinkGas(T=362.309)` |
| `cp_gas_j_kg_k` | 1100.0 | NG combustion products at HRSG-avg temperature |
| `T_exh_nominal_K` | 843.0 | ThermoPower `flueGasNomTemp` |

CCPP default rated power: **338.7 MW** = analytical output at full load with 235 MW GT topping cycle, before any size scaling.

### 4.3  Validation

`tools/build_surrogate/validate_ccpp.py` runs 5 sanity checks. Captured output:

```
Full-load efficiency: 0.5713 (57.13%)
PASS: full-load overall efficiency is in modern-CCPP band (55-62%).

Full-load P_ST/P_GT ratio: 0.441
PASS: ST/GT power ratio is in industry-typical range (0.35-0.55).

@ load=0.8: CCPP fuel=10.3677 kg/s, expected=10.3677 kg/s, CCPP CO2=28.5113 kg/s, expected=28.5113 kg/s
PASS: CCPP fuel and CO2 match the scaled GT values (no supplementary firing).

Eta_bottoming: full=0.3200, half=0.2872, low=0.2217
PASS: bottoming-cycle efficiency derates with load as expected.

Mixed Fleet (GT, CCPP, GT) @ 0.7: P=576.9 MW, fuel=28.51 kg/s, eff=0.413
PASS: CombinedCyclePlant is swappable with GasTurbinePlant in Fleet.

Wrote tools/build_surrogate/validation/ccpp_vs_gt.png
```

Cross-checks (re-derived by hand):
- **57.1% overall efficiency** at full load matches modern CCPP performance for a single-shaft 1×1 plant. Top-end commercial CCPPs reach 62-64%; defaults here are conservative (η_bot 0.32 not 0.36).
- **P_ST / P_GT = 0.441** corresponds to the bottoming cycle adding ~30% to total output — directly consistent with the η_bot × ΔT_HRSG arithmetic given the 235 MW topping cycle.
- **η_bottoming**: 32.0% (full), 28.7% (half), 22.2% (low). The derate is linear in `(T_exh − T_stack)` and traces the exhaust-temperature plateau-then-ramp of the GT surrogate. Above GTLoad=0.6, T_exh is constant at 843 K, so η_bot plateaus at the nominal 32%. Below GTLoad=0.6, T_exh ramps down, so η_bot ramps down too.
- **Mixed Fleet** (`Fleet([GT, CCPP, GT])` @ 0.7) returns 576.9 MW total: 2×164.5 MW (GT@0.7) + 247.9 MW (CCPP@0.7) = 576.9 MW ✓.

The validation plot (`tools/build_surrogate/validation/ccpp_vs_gt.png`) shows four panels:

1. CCPP electrical output is always above the GT line by the bottoming-cycle uplift; both ramp linearly from idle to full load.
2. Efficiency curves: GT reaches ~40% at full load, CCPP reaches the bottom of the green "modern CCPP" band (55-62%) at full load.
3. Topping vs. bottoming decomposition: P_ST grows with load and tracks the GT exhaust energy. The decomposition shows the bottoming cycle contributes increasingly from idle to full load.
4. η_bottoming vs. load: derate kicks in below GTLoad=0.6 (where T_exh starts dropping), plateaus at 32% above.

## Phase 5: packaging

### 5.1  Repo layout

The runtime component is now self-contained under `gas_plant/`. Anything outside the package (`tools/`, vendored libs, MSL) is build-time infrastructure that the larger tool never imports.

```
DataCenter/
├── gas_plant/                              # the runtime package
│   ├── __init__.py                         # exports GasTurbinePlant, CombinedCyclePlant, Fleet
│   ├── unit.py                             # GasTurbinePlant
│   ├── combined_cycle.py                   # CombinedCyclePlant
│   ├── fleet.py                            # Fleet (works with both unit types)
│   └── data/
│       └── gas_turbine_surrogate.csv       # bundled — package is portable
├── tests/
│   └── smoke_test.py                       # end-to-end API exercise
├── data/                                   # build-time copy (Phase 2 sweep output)
│   └── gas_turbine_surrogate.csv
├── tools/build_surrogate/                  # offline ThermoPower pipeline
│   ├── libs/                               # vendored MSL 3.2.3
│   ├── load_check.mos
│   ├── validate_gt.mos
│   ├── sweep_gt.mos
│   ├── check_ccpp.mos
│   ├── validate_surrogate.py
│   ├── validate_ccpp.py
│   └── validation/
│       ├── surrogate_vs_modelica.png
│       └── ccpp_vs_gt.png
├── ThermoPower/                            # vendored Modelica library
├── environment.yml                         # `conda env create -f environment.yml`
├── pyproject.toml                          # `pip install -e .` if desired
├── phase1_setup.md
├── phase2_3_setup.md
└── phase4_5_setup.md                       # this file
```

### 5.2  Smoke test

`tests/smoke_test.py` exercises every public entry point: scalar/array/profile dispatch for both unit types, homogeneous and mixed-type fleets, bad-input rejection, and bundled-data lookup.

```bash
~/miniconda3/envs/datacenter/bin/python tests/smoke_test.py
```

Captured output:

```
test_package_data_bundled
  Bundled surrogate found at /Users/syellapa/Documents/Research/2026/DataCenter/gas_plant/data/gas_turbine_surrogate.csv

test_scalar_dispatch_gt
  GT @ 0.7: 164.5 MW, eff=0.353

test_scalar_dispatch_ccpp
  CCPP @ 1.0: 338.7 MW, eff=0.571

test_array_dispatch
  GT array dispatch: [ 70.5 141.  211.5]

test_dispatch_profile
  Profile: peak=235.0 MW over 24h

test_fleet_homogeneous
  Homogeneous Fleet (4 GTs) @ 0.8: 752.0 MW

test_fleet_heterogeneous_mixed_types
  Mixed Fleet [GT@0.6, CCPP@0.9]: 449.4 MW, eff=0.462

test_rejects_bad_inputs
  Bad inputs (out-of-range, NaN) correctly rejected.

All smoke tests passed.
```

### 5.3  How the larger tool integrates

```python
# In the larger DataCenter tool, anywhere:
from gas_plant import GasTurbinePlant, CombinedCyclePlant, Fleet

# Construct units (defaults match ThermoPower's 235 MW GT and ~339 MW CCPP)
gt   = GasTurbinePlant()
ccpp = CombinedCyclePlant()

# Scalar dispatch
ccpp.dispatch(0.7)
# {'power_w': 247_900_000.0, 'fuel_kg_s': 9.50,
#  'efficiency': 0.532, 'exhaust_m_kg_s': 494.0,
#  'exhaust_T_K': 363.0, 'co2_kg_s': 26.13}

# Time-series dispatch
import pandas as pd, numpy as np
load = pd.Series(np.linspace(0.4, 1.0, 24),
                 index=pd.date_range("2026-01-01", periods=24, freq="h"))
ccpp.dispatch_profile(load)         # → DataFrame, 24 rows × 6 cols

# Fleet — any mix of unit types
fleet = Fleet([GasTurbinePlant(), CombinedCyclePlant(), CombinedCyclePlant()])
fleet.dispatch(0.8)                                   # same load to all
fleet.dispatch(np.array([1.0, 0.6, 0.3]))             # per-unit loads
fleet.dispatch_profile(load)                          # broadcast over time
```

### 5.4  Environment & install

Two ways to provision the runtime:

```bash
# Option A: conda (matches what was built)
conda env create -f environment.yml
conda activate datacenter

# Option B: pip into any environment (uses pyproject.toml)
pip install -e .
```

Runtime deps are `numpy`, `pandas`, `scipy` — nothing else. No FMPy, no PyFMI, no Modelica, no Docker. The OpenModelica / Colima toolchain only exists in `tools/build_surrogate/` and is touched only when the surrogate table needs to be regenerated.

## Loose ends and follow-ups

- `~/.condarc` typo (`confa-forge`) still present — bypassed throughout via `--override-channels`. Recommend the user fix this for general conda hygiene.
- The `data/` directory at project root holds a duplicate of the surrogate CSV (the Phase 2 build artifact). The runtime package reads only from `gas_plant/data/`. Two options: delete the root-level copy, or treat it as the canonical build output and have a future regeneration step copy it into the package.
- `cp_gas_j_kg_k = 1100` is a single average value; for higher accuracy at the low-load end (where T_exh < 700 K), a temperature-dependent cp could be substituted. Current default is conservative enough for dispatch-class accuracy.
- The CCPP model assumes no supplementary HRSG firing. Adding that would mean a second fuel input — easy to layer on if needed.
- If/when ThermoPower upstream fixes the `J=J_shaft` bug, the analytical CCPP could be cross-validated against the patched CCPP_Sim3 over a small load grid. The analytical defaults are tuned to ThermoPower's own design-point assumptions, so good agreement is expected.

## Status

All 11 tasks closed. The `gas_plant` package is ready for the larger tool to import.

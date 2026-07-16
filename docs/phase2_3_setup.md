# Phases 2–3 — simple-cycle gas turbine surrogate + Python component

Operational record of building the simple-cycle GT surrogate and the runtime Python classes. All commands captured below were run on 2026-05-21 against OpenModelica 1.26.7 (Docker, see `phase1_setup.md` for the toolchain).

## Phase 2: surrogate generation

### 2.1  Python environment (conda)

Created a fresh `datacenter` env from conda-forge with runtime deps only — no FMPy/PyFMI, so Modelica never appears in the runtime import path.

```bash
conda create -n datacenter -c conda-forge --override-channels \
  python=3.12 numpy pandas scipy matplotlib -y
```

(`--override-channels` was needed because `~/.condarc` had a typo: `confa-forge` instead of `conda-forge`. The typo affects all conda commands until fixed.)

Verification:

```
python 3.12.13
numpy 2.4.6
pandas 3.0.3
scipy 1.17.1
matplotlib 3.10.9
```

### 2.2  Native validation at three reference load points

`tools/build_surrogate/validate_gt.mos` simulates `ThermoPower.PowerPlants.GasTurbine.Tests.TestGasTurbine` at GTLoad ∈ {1.0, 0.6, 0.3}, overriding the `Constant` source `const.k` per call. `GasTurbineSimplified` is purely algebraic (no state derivatives), so `stopTime=1` is sufficient — the answer is identical at every t.

```bash
docker run --rm -v "$PWD:/work" -w /work \
  -e OPENMODELICALIBRARY=/work/tools/build_surrogate/libs \
  openmodelica/openmodelica:v1.26.7-minimal \
  omc /work/tools/build_surrogate/validate_gt.mos
```

Captured output (filtered to the values):

```
--- GTLoad = 1.0 ---
P_el           = 2.35e+08 W
fuelFlowRate   = 12.1 kg/s
exhaust m_flow = 614 kg/s
exhaust T      = 843 K

--- GTLoad = 0.6 ---
P_el           = 1.41e+08 W
fuelFlowRate   = 8.63793 kg/s
exhaust m_flow = 454 kg/s
exhaust T      = 843 K

--- GTLoad = 0.3 ---
P_el           = 7.05e+07 W
fuelFlowRate   = 6.36571 kg/s
exhaust m_flow = 454 kg/s
exhaust T      = 695.5 K
```

These match `PowerPlants.mo:171–189` exactly across all three operating regimes:

| GTLoad | Regime | Expected behaviour | Observed |
|---|---|---|---|
| 1.0 | nominal | full design point | 235 MW / 12.1 kg/s / 614 kg/s / 843 K ✓ |
| 0.6 | constTempLoad boundary | exhaust flow plateau ends, T at max | 141 MW / 8.64 kg/s / 454 kg/s / 843 K ✓ |
| 0.3 | below intLoad (0.42) | min-flow plateau, T below max | 70.5 MW / 6.37 kg/s / 454 kg/s / 695.5 K ✓ |

A cosmetic warning appears in stderr ("`Parameter const.k has no value, ... using available start value (start=1)`") — this is OpenModelica complaining that MSL's `Modelica.Blocks.Sources.Constant` declares `k` with `start=` but no value annotation; the `-override` clearly takes effect (otherwise all three sims would have produced identical 235 MW output).

### 2.3  21-point GTLoad sweep → surrogate CSV

`tools/build_surrogate/sweep_gt.mos` loops over 21 load points (0.00 to 1.00 in 0.05 increments), calls `simulate(...)` with the corresponding `-override`, and accumulates a CSV in memory before writing once at the end. The CSV is written to `/work/data/gas_turbine_surrogate.csv` — visible on the host because the project dir is bind-mounted.

```bash
docker run --rm -v "$PWD:/work" -w /work \
  -e OPENMODELICALIBRARY=/work/tools/build_surrogate/libs \
  openmodelica/openmodelica:v1.26.7-minimal \
  omc /work/tools/build_surrogate/sweep_gt.mos
```

Output (`data/gas_turbine_surrogate.csv` contents):

```
GTLoad,P_el_W,fuelFlowRate_kg_s,exhaust_m_flow_kg_s,exhaust_T_K
0,0,4.58,454,548
0.05,1.175e+07,4.87762,454,572.583
0.1,2.35e+07,5.17524,454,597.167
0.15,3.525e+07,5.47286,454,621.75
0.2,4.7e+07,5.77048,454,646.333
0.25,5.875e+07,6.0681,454,670.917
0.3,7.05e+07,6.36571,454,695.5
0.35,8.225e+07,6.66333,454,720.083
0.4,9.4e+07,6.96095,454,744.667
0.45,1.0575e+08,7.33966,454,769.25
0.5,1.175e+08,7.77241,454,793.833
0.55,1.2925e+08,8.20517,454,818.417
0.6,1.41e+08,8.63793,454,843
0.65,1.5275e+08,9.07069,474,843
0.7,1.645e+08,9.50345,494,843
0.75,1.7625e+08,9.93621,514,843
0.8,1.88e+08,10.369,534,843
0.85,1.9975e+08,10.8017,554,843
0.9,2.115e+08,11.2345,574,843
0.95,2.2325e+08,11.6672,594,843
1,2.35e+08,12.1,614,843
```

Per-simulate compile time was ~0.6 s (the model recompiles each call); total wallclock for 21 points ≈ 15 s. The kinks at GTLoad=0.42 (fuel slope change) and 0.6 (exhaust mass-flow plateau ends, exhaust temperature plateau begins) are visible in the data and match the equations.

## Phase 3: Python component

### 3.1  Package layout

```
gas_plant/
├── __init__.py          # exports GasTurbinePlant, Fleet
├── unit.py              # GasTurbinePlant — surrogate interpolation
└── fleet.py             # Fleet — aggregate N units
```

The runtime dependency set is just `numpy`, `pandas`, `scipy` (for `interp1d`). No FMPy, no PyFMI, no Modelica.

### 3.2  Round-trip validation

`tools/build_surrogate/validate_surrogate.py` is the harness — it loads the surrogate, evaluates at the original sweep nodes (must match to floating-point), runs scalar/profile dispatch, exercises Fleet, checks rated-power scaling, and produces the validation plot.

```bash
~/miniconda3/envs/datacenter/bin/python tools/build_surrogate/validate_surrogate.py
```

Captured output verbatim:

```
Max abs diff at sweep nodes (must be ~0):
  P_el_W          = 0.000e+00
  fuel_kg_s       = 0.000e+00
  exhaust_m_kg_s  = 0.000e+00
  exhaust_T_K     = 0.000e+00
PASS: surrogate reproduces Modelica outputs at sweep nodes.

Scalar dispatch @ GTLoad=0.5: 117.50 MW, 7.772 kg/s fuel, eff=0.309, CO2=21.374 kg/s
Profile dispatch (24 h ramp): peak=235.0 MW, fleet total fuel=786711 kg over 24h

Fleet repr: Fleet(3 units)
Fleet @ 0.8 (3 x 235 MW units): P=564.0 MW, fuel=31.11 kg/s, eff=0.370, mixed exhaust T=843 K
Fleet hetero [1.0, 0.6, 0.3]: P=446.5 MW, fuel=27.10 kg/s, per-unit P=[235.0, 141.0, 70.5]
Fleet profile: peak=670 MW, day-avg eff=0.343

Rated-power scaling check @ load=0.8: small/big power ratio = 0.4255, expected = 0.4255
PASS: power/fuel/exhaust-flow scale linearly with rated_power_mw; exhaust temperature is size-invariant.

Wrote tools/build_surrogate/validation/surrogate_vs_modelica.png
```

Interpretation:
- **Node match exact (0.000e+00):** `scipy.interpolate.interp1d` returns the exact node values at the original sample points — no FP drift. Between nodes the linear interpolant is the convex combination of neighbouring rows.
- **117.5 MW @ load=0.5 ✓:** matches the 0.5 row in the CSV (1.175e+08 W) exactly.
- **CO2 = 21.374 kg/s @ load=0.5:** 7.772 kg/s × 2.75 (NG factor) = 21.373 ✓.
- **Fleet of 3 @ 0.8 = 564 MW:** 3 × 188 MW = 564 MW ✓; efficiency 0.370 is the fleet-level energy-balance value, higher than the per-unit efficiency at 0.8 alone because aggregating doesn't change efficiency for homogeneous units (sanity check passes).
- **Heterogeneous fleet:** per-unit powers `[235, 141, 70.5]` MW match the CSV node values at GTLoad=1.0, 0.6, 0.3 exactly.
- **Rated-power scaling:** small/big = 100/235 = 0.4255 to 4 decimals; exhaust T identical (size-invariant) — as designed.

### 3.3  Validation plot

`tools/build_surrogate/validation/surrogate_vs_modelica.png` — six panels showing the Python surrogate (line) and the Modelica reference (red dots) for:

1. Electrical power [MW] — linear ramp 0 → 235.
2. Fuel flow [kg/s] — piecewise linear with kink at GTLoad=0.42 (intLoad).
3. Exhaust mass flow [kg/s] — flat 454 kg/s through GTLoad=0.6, then ramps to 614 kg/s.
4. Exhaust temperature [K] — linear ramp to 843 K at GTLoad=0.6, then plateau.
5. Thermal efficiency [-] — derived as `power / (fuel × LHV)`. Smooth concave curve reaching ~40% at full load; zero at GTLoad=0 (power=0 but fuel>0 idle consumption).
6. CO2 emissions [kg/s] — tracks fuel × 2.75.

All red dots lie exactly on the interpolant line.

## API delivered

```python
from gas_plant import GasTurbinePlant, Fleet

# Single unit, ThermoPower defaults (235 MW, NG @ 49 MJ/kg LHV, CO2 factor 2.75)
unit = GasTurbinePlant()
unit.dispatch(0.7)
# {'power_w': 164500000.0, 'fuel_kg_s': 9.503..., 'efficiency': 0.353...,
#  'exhaust_m_kg_s': 494.0, 'exhaust_T_K': 843.0, 'co2_kg_s': 26.13...}

# Time-series input
import pandas as pd, numpy as np
load_profile = pd.Series(
    np.linspace(0.3, 1.0, 24),
    index=pd.date_range("2026-01-01", periods=24, freq="h"),
)
unit.dispatch_profile(load_profile)        # → DataFrame, 24 rows × 6 cols

# Fleet of N units, same API
fleet = Fleet([GasTurbinePlant() for _ in range(3)])
fleet.dispatch(0.8)                         # scalar load to all units
fleet.dispatch(np.array([1.0, 0.6, 0.3]))   # per-unit loads
fleet.dispatch_profile(load_profile)        # broadcast same series to all units
```

Override knobs at construction:
- `rated_power_mw` (default 235) — linear scale for power, fuel, exhaust mass flow; exhaust temperature unaffected.
- `co2_per_fuel_kg` (default 2.75 kg CO2 / kg NG) — fuel-to-CO2 conversion factor.
- `fuel_lhv_j_kg` (default 49e6) — used only to compute efficiency.
- `table_path` (default `data/gas_turbine_surrogate.csv`) — point at an alternative surrogate CSV if you regenerate the sweep.

## Repo state after Phase 3

```
DataCenter/
├── ThermoPower/                                  # vendored Modelica library
├── data/
│   └── gas_turbine_surrogate.csv                 # 21-point sweep, Phase 2
├── gas_plant/                                    # runtime Python component
│   ├── __init__.py
│   ├── unit.py
│   └── fleet.py
├── tools/
│   └── build_surrogate/
│       ├── libs/                                 # vendored MSL 3.2.3
│       ├── load_check.mos
│       ├── validate_gt.mos
│       ├── sweep_gt.mos
│       ├── validate_surrogate.py
│       └── validation/
│           └── surrogate_vs_modelica.png
├── phase1_setup.md
└── phase2_3_setup.md                             # this file
```

## Loose ends carried into Phase 4

- `gas_plant/data/` directory is empty — the surrogate CSV currently lives at the project root `data/`. To be moved/bundled in Phase 5 packaging so the runtime package is self-contained.
- `~/.condarc` typo (`confa-forge`) flagged to the user; not changed.
- The simple-cycle surrogate produces "idle" outputs at GTLoad=0 (P=0 but fuel=4.58 kg/s) — this is realistic for spinning reserve; the larger tool can clamp at a higher floor if needed.
- A 24-hour ramp in the smoke test consumed 786,711 kg of NG, which corresponds to about 60 ML of fuel and 2,160 t of CO2 — sanity-check magnitudes for a 235 MW class plant at high duty.

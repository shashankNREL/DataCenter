# Gas Plant and Governor Model Critique and V&V Recommendations

## Task classification

This review is an analysis and documentation task. No code behavior was changed.

## Brutal summary

The repository has a credible research-prototype structure for gas plant and governor modeling, especially the staged Tier A/B/C/E progression in:

- `gas_plant/dynamics/tgov1.py`
- `gas_plant/dynamics/ggov1.py`
- `gas_plant/dynamics/multishaft.py`
- `gas_plant/dynamics/torsional.py`
- `docs/tier_plan.md`

However, the current models are not yet design-grade or prediction-grade. They are best described as:

> A credible exploratory dynamic modeling sandbox with decent documentation, but with many unvalidated assumptions and several places where physics are simplified enough that outputs should not be trusted quantitatively without additional validation.

The largest gap is not code organization. The largest gap is model credibility: parameter provenance, executable validation, comparison against independent tools, and actual field or OEM data.

## What is good

### 1. The staged model architecture is sensible

The progression from TGOV1 to GGOV1 to multishaft to torsional fatigue is directionally right.

- Tier A fixes obvious TGOV1 pathologies: anti-windup, valve rate limits, fuel-from-valve, and event-driven integration.
- Tier B moves toward GGOV1, which is more defensible for gas turbines than TGOV1.
- Tier C acknowledges that a free-power-turbine aeroderivative is not a single rigid rotor.
- Tier E correctly realizes that HP-to-PT coupling is gas-path, not a mechanical shaft, so torsional fatigue should focus on the PT-generator shaft.

### 2. The documentation is unusually transparent

`docs/tier_plan.md` records assumptions, sources, deviations, and known failures. That is exactly what is needed for engineering credibility.

### 3. Some fixes are physically motivated

Examples:

- Valve slew limits in TGOV1/GGOV1 are necessary.
- VMAX rebasing from generator MVA to turbine MW is correct.
- Fuel reporting from valve/combustor lag instead of mechanical output avoids double-counting turbine dynamics.
- Event-driven integration across zero-order-hold load discontinuities is numerically cleaner.

## Major problems and concerns

### 1. The current validation is mostly not real validation yet

The current validation is a mix of:

- smoke tests,
- monotonicity checks,
- datasheet full-load anchor checks,
- self-comparison between tiers,
- qualitative literature comparison.

That is useful, but it is not enough.

The most serious example is the Hannett Beluga 5 load rejection comparison documented in `docs/tier_plan.md`: the reported model response has an approximately 80x magnitude mismatch. The explanation about unknown MVA base and inertia is plausible, but that means the model has not actually been quantitatively validated against that event.

Calling the shape match "validation" is too generous. It is better described as a sanity check.

### 2. Many parameters are plausible, not identified

Examples include:

- `H_s = 2.8`
- `D_pu = 1.0`
- `Ropen = +/-0.10 pu/s`
- `T_comb = 0.3 s`
- `Kpgov = 10`
- `Kigov = 2`
- `Kturb = 1.5`
- `Wfnl = 0.2`
- HP/PT inertia split in Tier C
- torsional shaft geometry, material, and fatigue constants in Tier E

These are reasonable starting points, but many are not LM2500- or LM9000-identified. Sensitivity could be large.

### 3. The LM9000 model claims more physics than it actually contains

`gas_plant/lm9000.py` presents itself as a component-based thermodynamic stack, but the actual dispatch path is mostly empirical:

- power is linear with load,
- efficiency is a simple quadratic,
- exhaust temperature is fixed,
- air/fuel ratio is fixed,
- compressor/turbine maps are mostly declarative and not used in the dispatch calculation.

That is not a true component thermodynamic model. It is a calibrated algebraic performance surrogate with some component documentation around it.

This is acceptable if stated honestly, but the implementation should not imply it is first-principles.

### 4. Part-load behavior is weakly supported

The part-load curves are among the most important outputs for data-center duty cycles, but they are also among the least validated.

For simple-cycle and combined-cycle plants, the model needs actual or surrogate-backed curves for:

- heat rate vs load,
- exhaust temperature vs load,
- exhaust mass flow vs load,
- minimum stable load,
- fuel flow at full-speed/no-load,
- emissions vs load,
- ambient derate.

Right now, several of these are fixed, linear, or invented.

### 5. Combined-cycle physics are very simplified

`gas_plant/combined_cycle.py` and `LM9000CombinedCycle` use an analytical HRSG/bottoming-cycle layer. That is fine for dispatch-level energy accounting, but it ignores:

- steam drum and HRSG thermal inertia,
- steam pressure dynamics,
- steam turbine governor dynamics,
- attemperator behavior,
- stack temperature variation,
- pinch/approach constraints,
- startup and warm-state effects,
- combined-cycle ramp limits.

For electrical transients under seconds-scale load changes, a CCPP cannot be treated as "GT power plus instant steam-cycle uplift" unless the study is explicitly quasi-steady.

### 6. GGOV1 is approximate, not a full standard implementation

`gas_plant/dynamics/ggov1.py` captures the broad GGOV1 idea, but several details are simplified or missing:

- `rselect` is effectively not fully implemented.
- `KIMW` / MW controller path is not modeled.
- deadband is not modeled.
- `KDGOV` derivative path is not modeled.
- `TENG` delay is not modeled.
- limiter/rate blocks are simplified.
- temperature limiter uses a proxy, not a thermodynamic exhaust-temperature or firing-temperature model.
- anti-windup back-calculation gains are arbitrary and may strongly affect dynamics.

This is fine for a custom GGOV1-like model, but it should not be claimed as bit-level equivalent to a commercial GGOV1 implementation.

### 7. Tier C multishaft physics are directionally right but crude

The Tier C idea is good: separate HP rotor and PT/generator rotor. But the gas-path coupling is extremely simplified:

```text
P_couple = K_couple * (omega_hp - omega_hp_idle)
```

That is not compressor/turbine matching. It ignores:

- compressor map,
- surge margin,
- variable stators,
- fuel schedule,
- firing temperature limit,
- mass-flow dynamics,
- pressure ratio dynamics,
- bleed and cooling flows.

Tier C may produce qualitatively plausible additional delay, but the exact frequency nadir and HP speed response should not be treated as validated.

### 8. The ANDES path is behind the scipy path

`gas_plant_andes/case_builder.py` still uses:

- `GENCLS`
- `TGOV1`
- heavy-duty-ish defaults

It does not yet reflect the Tier B/C GGOV1/multishaft work. That means the electrical DAE path and the custom scipy dynamics path are not cross-validating the same model.

This is a major gap if the goal is power-system credibility.

### 9. Test coverage is too thin

`tests/smoke_test.py` mostly checks:

- API behavior,
- monotonicity,
- basic datasheet anchors,
- fleet compatibility.

There are no strong regression tests for governor dynamics, controller arbitration, steady-state residuals, load rejection, numerical convergence, or parameter sensitivity.

The notebooks and docs contain validation claims, but those claims are not executable tests.

## Additional verification to implement

Verification asks: "Did we build the equations correctly?"

### 1. Steady-state residual tests

For TGOV1, GGOV1, and multishaft models, add tests that initialize at a load and verify:

- `omega = 1.0`
- `Pm = Pe`
- derivative norm near zero
- valve/fuel/turbine states are consistent
- no drift over 30 to 300 seconds

Run at multiple loads: 15%, 25%, 50%, 75%, and 95%.

### 2. Scalar/vector equivalence tests

For every dispatch and dynamic post-processing path:

- scalar input and one-element array input should agree,
- array results should match repeated scalar calls,
- no silent shape changes should occur.

### 3. Per-unit base consistency tests

The repository mixes:

- generator MVA base,
- turbine MW base,
- system MVA base,
- rated plant MW,
- load fraction.

Add tests that assert conversions are correct, especially around:

- `Sn_mva`
- `P_turbine_mw`
- `Vmax_pu`
- `Kturb`
- fuel callback load fraction.

### 4. Controller limiter tests

For GGOV1:

- force speed-governor selection,
- force acceleration-controller selection,
- force temperature-limiter selection,
- verify low-value gate selection,
- verify anti-windup states track correctly,
- verify valve clamps and slew limits are respected.

The existing Load17 profile does not strongly exercise acceleration and temperature limiter paths.

### 5. Numerical convergence tests

For representative events, rerun with:

- smaller `sample_dt_s`,
- tighter `rtol` / `atol`,
- smaller `max_step_s`.

Accept only if key metrics converge:

- frequency nadir,
- RoCoF,
- settling time,
- valve peak,
- fuel integral,
- HP speed peak,
- shaft torque peak.

### 6. Linearization and eigenvalue checks

For TGOV1, GGOV1, and multishaft models around steady state:

- numerically linearize the ODE,
- check eigenvalues,
- verify no positive real parts,
- identify dominant modes,
- compare time-domain step response to linearized response for small perturbations.

This would catch many sign errors and hidden instabilities.

## Additional validation to implement

Validation asks: "Is this the right model for the real plant?"

### 1. Static gas turbine performance-map validation

For LM2500 and LM9000, validate against independent curves or generated references for:

- power vs load,
- heat rate vs load,
- fuel flow vs load,
- exhaust temperature vs load,
- exhaust mass flow vs load,
- ambient temperature derate,
- minimum emissions-compliant load.

Potential sources:

- OEM datasheets,
- GT PRO / Thermoflex outputs,
- public EPA or permit data,
- operator heat-rate curves,
- literature part-load maps.

Suggested acceptance criteria:

| Quantity | Target tolerance |
|---|---:|
| full-load power | +/-1-2% |
| full-load heat rate | +/-2% |
| part-load heat rate | +/-5% |
| exhaust temperature | +/-15-25 K |
| exhaust mass flow | +/-5-10% |

### 2. Governor model validation against standard references

Before validating against LM2500, validate the GGOV1 implementation against another GGOV1 implementation:

- ANDES GGOV1 if available,
- PSS/E,
- PowerWorld,
- PSLF,
- OpenIPSL/Modelica.

Use identical parameters and compare response to:

- small load step,
- large load step,
- load rejection,
- speed reference change,
- valve saturation,
- acceleration limit,
- temperature limit.

This separates "plant parameters are wrong" from "GGOV1 equations are wrong."

### 3. Hannett validation with proper normalization

The current Hannett comparison is not strong because base MVA and inertia are unknown or mismatched.

Implement a parameter-estimation version:

- use digitized Hannett speed curve,
- use digitized fuel/valve curve,
- fit `H`, `D`, `Tact`, `Kpgov`, `Kigov`, `Kturb`, `Wfnl`, and rate limits,
- report confidence intervals and parameter identifiability.

If many parameter combinations fit equally well, that is important evidence.

### 4. LM2500 block-load acceptance validation

For the actual study, the most relevant validation is block-load response:

- 0 to 25% load,
- 25 to 50%,
- 50 to 75%,
- 75 to 100%,
- 100 to 50% rejection,
- full-load rejection.

Validate:

- frequency nadir/overshoot,
- RoCoF,
- recovery time,
- valve trajectory,
- fuel flow trajectory,
- HP rotor speed trajectory,
- exhaust temperature / T5.4 if available.

### 5. Frequency protection validation

The data-center islanding problem is protection-sensitive. Add validation around:

- under-frequency trip thresholds,
- over-frequency trip thresholds,
- load-shed thresholds,
- governor saturation,
- black-start/restart constraints,
- minimum fuel / flameout floor.

The model can produce frequency values, but it does not yet include a serious relay/protection layer.

### 6. Data-center load model validation

The load model is probably as important as the turbine.

Validate:

- workload-to-power conversion,
- sampling rate,
- anti-aliasing,
- UPS/PDU smoothing,
- server PSU ride-through,
- voltage dependence,
- motor/fan/chiller fraction,
- battery/flywheel response if present.

A constant-power data-center load is a harsh assumption. It may be conservative for frequency but wrong for voltage and fault studies.

### 7. Combined-cycle dynamic validation

If combined-cycle plants are used dynamically, validate separately:

- GT-only response,
- steam turbine delayed response,
- HRSG lag,
- CCPP load ramp,
- stack temperature,
- steam contribution after load step.

Do not let the steam turbine respond instantly unless the study is explicitly quasi-steady.

### 8. Torsional validation

For Tier E, compare to:

- expected torsional natural frequency from shaft train study,
- OEM torsional report if available,
- generator short-circuit torque envelope,
- breaker close/reclose transient,
- motor-start torque impulse,
- grid fault clearing event.

Load17-style slow demand changes are not a meaningful torsional fatigue validation case. Faults and switching events are.

## Recommended priority order

1. Turn the documented Tier A/B/C/E validation cases into executable regression tests.
2. Cross-check GGOV1 against an independent implementation.
3. Validate static LM2500/LM9000 part-load heat-rate and exhaust curves.
4. Fit governor parameters against one real or literature load-rejection event.
5. Upgrade the ANDES path to GENROU/EXST1/GGOV1 and compare against scipy.
6. Add sensitivity and uncertainty sweeps for uncertain parameters.
7. Only then use the model for claims about data-center islanding margins.

## Bottom line

The codebase is promising, but the current model should be presented as exploratory engineering simulation, not a validated digital twin.

The strongest parts are the architecture and documentation. The weakest parts are:

- insufficient executable validation,
- many uncalibrated parameters,
- crude gas-path and part-load physics,
- weak CCPP dynamics,
- ANDES/scipy mismatch,
- too much reliance on qualitative agreement.

Implementing the V&V recommendations above, especially independent GGOV1 cross-checking and real/static part-load validation, would make the project much more defensible.

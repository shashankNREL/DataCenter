# Transformer Thermal, Oil Degradation, and Life Modeling Plan

Status: proposed research and implementation plan; no model implementation is authorized by this document.

Prepared: 2026-10-07.

Source reviewed: [Transformer Thermal and Degradation Modeling Plan](Transormer_Thermal_and_Degradation_Modeling_Plan.docx).

## 1. Executive recommendation

Retain the source document's thermal and paper-aging framework, correct its treatment of harmonic losses, and add a separate, experimentally calibrated oil-condition model. Do not equate paper aging, oil maintenance interval, and transformer failure time.

The recommended sequence is:

1. Establish transformer topology, parameters, fluid type, and available measurements.
2. Compare workload strategies using a calibrated thermal and paper-aging model.
3. Identify oil-degradation kinetics using laboratory and/or field measurements.
4. Forecast condition-based maintenance thresholds with uncertainty and explicit intervention scenarios.
5. Evaluate whether scheduling, buffering, cooling, or oil maintenance provides a meaningful benefit.

An initial workload study can produce useful comparative results without claiming an absolute oil lifetime. Predicting years of oil service requires evidence about oil chemistry, preservation system, initial condition, and future operating exposure.

## 2. Assessment of the source plan

| Source-plan element | Assessment | Required refinement |
| --- | --- | --- |
| Top-oil and winding hot-spot dynamics | Appropriate starting point for a reduced-order thermal model | Identify cooling class, calibrate thermal constants, handle initialization and changing ambient temperature |
| Rated-load temperature-rise relationships | Useful baseline | Verify the applicable standard formulation and parameter definitions for the actual asset |
| Multiplication of load heating by one harmonic loss factor | Too broad if applied to every load-loss component | Separate resistive, winding-eddy, and other stray losses |
| Hot-spot Arrhenius aging factor | Estimates temperature-driven paper-insulation aging under its assumptions | Match paper type and reference-life definition; do not reuse it for oil chemistry |
| 110 degree C reference temperature | Conditional insulation-model reference | Verify applicability to the insulation system rather than treating it as universal |
| 180,000-hour normal-life assumption | Reference denominator, not an asset-specific lifetime | Report equivalent aging hours first; identify the assumed insulation endpoint if reporting percent loss of life |
| Oil degradation | Not explicitly modeled | Add inhibitor, oxidation, moisture, and condition-observation models supported by data |
| Maintenance and uncertainty | Not included | Represent interventions and prediction intervals explicitly |

IEEE identifies C57.91-2025 as the successor to C57.91-2011. The public scope and revision status were checked; the full normative text and applicable OEM requirements must be reviewed before selecting equations, parameters, or operating limits.

## 3. Objectives and definitions

### 3.1 Separate life-related outputs

| Output | Meaning | What it does not establish |
| --- | --- | --- |
| Equivalent paper-aging hours | Accumulated reference-temperature aging exposure | Measured remaining mechanical strength or a guaranteed retirement date |
| Oil serviceability interval | Time until a specified condition-based intervention threshold is reached | Unavoidable oil replacement or transformer failure |
| Abnormal-condition indicators | Evidence of overheating, discharge, contamination, or dielectric weakness | Ordinary oxidation aging alone |
| Transformer reliability assessment | Combined consideration of insulation, cooling, bushings, tap changers, faults, and other components | A quantity obtainable from oil acidity or paper aging alone |

Define oil life operationally as **time to an agreed maintenance threshold under an explicit operating and maintenance scenario**. Different thresholds may lead to inspection, drying, filtration, reclamation, reinhibition, or replacement.

### 3.2 Research questions

1. Does workload staging reduce transformer temperatures and cumulative thermal exposure when equivalent work is completed?
2. Are sustained loading and seasonal ambient conditions more influential than subsecond workload ramps?
3. How much additional heating comes from measured harmonic currents, independently of workload variability?
4. How do oxygen exposure, inhibitor depletion, and moisture affect the oil maintenance interval?
5. Which intervention is most effective per unit of cost: scheduling, BESS placement, cooling, loading headroom, or oil treatment?
6. How sensitive are conclusions to uncertain transformer parameters, initial oil condition, and future workloads?

### 3.3 Initial scope and exclusions

Start with one identified mineral-oil-immersed transformer or a small set of comparable assets. Record inhibited/uninhibited oil, sealed/breathing preservation system, paper type, cooling arrangement, service history, and initial oil condition.

Natural esters, synthetic esters, silicone fluids, and mixed fluids need fluid-specific chemistry and moisture interpretation. Do not transfer mineral-oil coefficients to these fluids without validation.

The baseline model does not predict lightning damage, short-circuit mechanical failure, partial-discharge progression, bushing failure, or protection performance. Inrush and fault heating require separate event studies where relevant.

## 4. Modeling architecture

The physical and observation chain is:

**Workload and network topology -> phase currents and losses -> oil/winding temperatures -> paper aging and oil chemistry -> condition observations -> maintenance decisions.**

Keep the thermal, paper-aging, oil-chemistry, and diagnostic layers distinct. This permits a useful thermal study before oil-life calibration is available and avoids giving one unvalidated health index several incompatible meanings.

### 4.1 Electrical loading and harmonic losses

Prefer measured phase RMS currents, voltages, real/reactive power, and harmonic spectra at the transformer. Model unequal phase loading and unequal sharing between parallel transformers where material.

For balanced, approximately sinusoidal conditions, apparent power can be estimated from real and reactive power. Under distorted conditions, use measured true RMS quantities and true power factor. GPU/facility MW traces alone do not identify transformer RMS current or harmonic heating.

Define per-unit current loading as:

$$
K = \frac{I_{\mathrm{rms}}}{I_R}, \qquad I_{\mathrm{rms}}^2 = \sum_h I_h^2.
$$

A useful loss decomposition, initially at the rated reference temperature, is:

$$
P_{\mathrm{loss}} = P_0 + K^2\left(P_{\mathrm{dc},R} + F_E P_{\mathrm{eddy},R} + F_S P_{\mathrm{stray},R}\right).
$$

Here, $P_0$ is no-load loss and the other rated quantities are resistive, winding-eddy, and other stray loss components. Harmonic factors must be normalized consistently with total RMS current, so current distortion is not counted twice.

Common power-frequency screening relationships use:

$$
F_E = \frac{\sum_h h^2 I_h^2}{\sum_h I_h^2}, \qquad F_S = \frac{\sum_h h^{0.8} I_h^2}{\sum_h I_h^2}.
$$

These expressions are candidate IEEE C57.110-based relationships, not universal frequency-response laws. Verify their applicability, frequency range, loss definitions, and conservative treatment against the selected standard and transformer design. Switching-frequency losses may need additional manufacturer information.

Add temperature corrections to the loss components using appropriate material/design relationships. Do not assume that resistive and eddy losses have the same temperature dependence. Keep voltage/frequency-dependent core loss fixed only when the supply assumptions justify it.

If the rated loss split is unknown, use documented bounds and sensitivity analysis rather than inventing a precise split. Record whether each parameter is measured, supplied by the OEM, inferred, or assumed.

### 4.2 Thermal dynamics

For fixed ambient temperature and cooling mode, the source plan's baseline is:

$$
\tau_{\mathrm{TO}}\frac{d\Delta\theta_{\mathrm{TO}}}{dt} = \Delta\theta_{\mathrm{TO},U} - \Delta\theta_{\mathrm{TO}}, \qquad
\tau_H\frac{d\Delta\theta_H}{dt} = \Delta\theta_{H,U} - \Delta\theta_H.
$$

$$
\theta_{\mathrm{TO}} = \theta_A + \Delta\theta_{\mathrm{TO}}, \qquad
\theta_H = \theta_{\mathrm{TO}} + \Delta\theta_H.
$$

A loss-aware screening relationship for ultimate top-oil rise is:

$$
\Delta\theta_{\mathrm{TO},U} = \Delta\theta_{\mathrm{TO},R}\left(\frac{P_{\mathrm{loss}}}{P_{\mathrm{loss},R}}\right)^n.
$$

Hot-spot rise must use the winding-heating relationship appropriate to the chosen standard/model; the source's $K^{2m}$ relationship is the sinusoidal-load baseline, not sufficient evidence for harmonic hot-spot prediction.

For changing ambient conditions, use a consistent absolute-temperature energy balance or properly transform the rise-state equations. Adding a rapidly changing ambient temperature to a lagged rise state can otherwise produce artificial instantaneous jumps in absolute temperature. The same consistency is needed when coupling winding and oil states.

Required refinements:

- Calibrate rated rises, thermal exponents, and time constants using heat-run results or field transients.
- Represent ONAN/ONAF or other relevant cooling modes, fan/pump thresholds, hysteresis, and cooling availability.
- Initialize from measured temperatures or a realistic warm-up history; do not start every scenario cold.
- Include altitude, enclosure, radiator fouling, and air recirculation where they materially change cooling.
- Advance the model at load/cooling discontinuities and carry thermal states between days and scenarios.
- Add bottom-oil, duct-oil, or additional winding states only when simpler models fail relevant validation.

The reduced-order model should be described as a calibrated screening model unless it has been shown to implement and satisfy the selected standard formulation.

### 4.3 Paper-insulation aging

For the thermally upgraded paper assumptions used in the source document, the candidate reference relationship is:

$$
F_{AA}(t) = \exp\left(\frac{15000}{383} - \frac{15000}{\theta_H(t)+273}\right), \qquad L_{\mathrm{eq}} = \int F_{AA}(t)\,dt.
$$

This reproduces the source's rounded temperature convention; use the selected standard's convention consistently in implementation. Verify the coefficient, reference temperature, insulation class, and applicability before adoption. Integrating with time in hours produces equivalent aging hours.

Report $L_{\mathrm{eq}}$ and $L_{\mathrm{eq}}/t_{\mathrm{horizon}}$ first. Percent reference loss of life is:

$$
\mathrm{LoL}_{\mathrm{reference}} = 100\frac{L_{\mathrm{eq}}}{L_{\mathrm{reference}}}.
$$

Treat $L_{\mathrm{reference}}$ as an explicit insulation-model assumption. It is neither oil life nor guaranteed remaining transformer life. Existing assets also require an estimate of prior degradation before remaining-life claims can be considered.

Initially assess paper moisture and oxygen as sensitivity scenarios. Introduce quantitative corrections only when supported by a validated relationship for the relevant insulation. A later paper-condition model may use degree of polymerization and cellulose markers, recognizing that furan concentration is affected by paper type, oil treatment, and partitioning.

### 4.4 Oil-degradation kinetics

Use a small identifiable model before attempting a comprehensive reaction network.

| State or quantity | Physical role | Calibration evidence |
| --- | --- | --- |
| Inhibitor concentration | Tracks depletion of oxidation protection in inhibited oil | Serial inhibitor measurements |
| Oxidation/acid indicator | Tracks formation of degradation products | Acidity and complementary chemistry measurements |
| Dissolved oxygen or oxygen exposure | Distinguishes sealed and breathing behavior | Preservation-system information and oxygen measurements where available |
| Oil moisture and, later, paper moisture | Tracks dielectric condition and water exchange | Moisture, temperature, relative saturation, and relevant paper evidence |
| Sludge/deposit indicator, optional | Represents late-stage products and possible cooling impairment | Deposit/sludge evidence and thermal-performance observations |

A candidate rate structure is:

$$
r_{\mathrm{ox}} = A\exp\left(-\frac{E_a}{R_g T_{\mathrm{oil}}}\right) f(c_{O_2})g(c_{\mathrm{inh}}).
$$

$T_{\mathrm{oil}}$ is in kelvin, $R_g$ is the gas constant, and the pre-exponential factor, activation energy, oxygen response, and inhibitor response must be identified from data. Do not reuse the paper-aging coefficient or assume all indicators follow the same kinetics.

Candidate state balances include inhibitor consumption, formation/removal of an oxidation indicator, and oxygen ingress/consumption. Their coefficients and observation relationships must be fitted independently where data allow. An acid-formation state does not by itself predict interfacial tension, dielectric loss, or breakdown voltage.

Use measured or estimated representative bulk-oil temperature. Applying winding hot-spot temperature to the entire oil volume overstates its exposure. If spatial temperature differences matter, compare a calibrated multi-zone model against a single bulk temperature; top-oil exposure may be a sensitivity case, not a guaranteed bound on all chemical behavior.

Start with constant or prescribed oxygen-exposure scenarios if oxygen dynamics cannot be identified. Introduce inhibitor induction periods, autocatalysis, copper-catalyzed oxidation, and sludge only when measurements justify them. Label the model empirical or semi-empirical rather than implying first-principles certainty.

### 4.5 Moisture and oil-paper coupling

Measure oil moisture in mass concentration and interpret it together with temperature-dependent relative saturation. Paper moisture is a separate quantity; instantaneous equilibrium is often inappropriate during temperature transients.

A later coupled model can represent temperature-dependent partitioning and finite-rate transfer between oil and paper, together with atmospheric ingress, water formation, and drying. Mass balance and concentration-to-water-mass conversions must account for oil and paper quantities.

Do not infer dielectric safety from moisture concentration alone. Temperature, particles, bubbles, fluid condition, and the test method also matter. Do not apply mineral-oil moisture thresholds to esters without appropriate interpretation.

### 4.6 Maintenance and observation models

Separate physical degradation states from measured indicators and include sampling uncertainty. Use oil tests to update model state/parameters; do not fit a lifetime from a single sample or an unqualified straight-line trend.

| Intervention | Representation | Important limitation |
| --- | --- | --- |
| Filtration | Reduction in removable particles | Does not necessarily remove dissolved acids or water |
| Drying/dehydration | Reduced oil moisture; slower paper-water removal where applicable | Paper moisture can rebound into oil |
| Reclamation | Measured reduction in selected degradation products | Does not automatically remove internal deposits or restore paper |
| Reinhibition | Measured increase in inhibitor concentration | Must account for existing oil condition and fluid compatibility |
| Oil replacement | New oil composition and condition, with mixing/retained-oil effects | Does not reset aged paper or transformer life |
| Cooling-system service | Changed heat rejection or cooling availability | Thermal improvement is not chemical rejuvenation |

Intervention thresholds should come from the applicable IEC 60422 guidance, OEM requirements, asset voltage/class, fluid type, trend, and utility practice. Avoid universal hard-coded thresholds. Retain separate warning/action thresholds and confirm sampling anomalies before making maintenance decisions.

## 5. Data collection plan

| Category | Minimum useful inputs | Higher-value additions |
| --- | --- | --- |
| Asset | Ratings, voltage, cooling class, fluid/paper type, preservation system, history | Geometry, oil/paper quantities, detailed loss split |
| Electrical | Phase RMS current, voltage, real/reactive power or true power factor | Harmonic spectra, unbalance, UPS/BESS operating modes |
| Thermal | Ambient, top-oil, cooling status, heat-run/rated-rise information | Winding fiber-optic temperatures, bottom-oil and radiator temperatures |
| Oil | Baseline and repeat acidity, moisture, inhibitor where applicable, interfacial tension | Dissipation factor/resistivity, oxidation products, particles, sludge evidence |
| Diagnostics | DGA and maintenance/event records | Furans and other cellulose markers interpreted for the insulation system |
| Operations | Workload trace, completed work, schedule duration, maintenance dates | Weather sequences, future workload ensembles, energy tariffs and SLAs |

Record measurement units explicitly: temperature in degree C/K, acidity in mg KOH/g, interfacial tension in mN/m, moisture in mg/kg and percent relative saturation, and inhibitor in the reported mass fraction or mass percent. Preserve laboratory method, detection limits, sampling temperature, and uncertainty.

Synchronize timestamps and document timezone, missing-data treatment, instrument location, CT/PT ratios, calibration, sampling procedure, and oil additions/treatment. Do not interpolate across maintenance as if the asset condition were continuous.

Existing project inputs are described in [data documentation](../data/README.md). GPU and scheduler traces are workload proxies, not transformer current, harmonic, or oil-condition measurements. Facility mapping must identify which auxiliary loads actually pass through the studied transformer.

## 6. Ways to study oil life: benefits and limitations

| Approach | Primary question | Pros | Cons | Recommended role |
| --- | --- | --- | --- | --- |
| Thermal exposure screening | Which workload creates less heating? | Fast, transparent, low data burden | Cannot establish chemical oil life | First implementation stage |
| Accelerated laboratory aging | How do temperature, oxygen, inhibitor, and materials affect kinetics? | Controlled mechanism comparisons and repeatable measurements | High-temperature extrapolation and vessel conditions may misrepresent service | Identify and test chemistry parameters |
| Field sampling and monitoring | How does actual oil condition evolve? | Direct operational relevance and maintenance context | Slow, confounded, often sparse | Validate laboratory-to-field transfer |
| Hybrid physics/statistical forecasting | When will a maintenance threshold be reached? | Combines exposure and measured condition; supports updates and uncertainty | Requires identifiable parameters and validation | Preferred forecasting approach |
| Fleet survival analysis | What predicts maintenance or failure across assets? | Can use long records and handle censoring | Asset differences, treatment, and replacement policies confound results | Complementary population-level evidence |
| Detailed thermal/CFD analysis | Are local temperature or circulation effects important? | Resolves spatial heating and design effects | High effort and uncertain boundary conditions; no standalone chemistry prediction | Escalation only when reduced models fail |

For fleet analysis, distinguish oil treatment, oil replacement, transformer retirement, and failure as separate outcomes. Handle censored records and competing risks, and do not label maintenance-policy differences as physical lifetime differences.

## 7. Experimental program

### 7.1 Workload and time-scale study

Reuse the workload strategies and retained time series from the [scheduler-controlled ramp demonstration](scheduler_ramp_demo.md). Evaluate immediate activation, staged admission, completion-aware scheduling, constant-load reference, and peak-capped scheduling.

Complete the same requested work subject to comparable SLA constraints. Account for extended runtime, facility idle energy, temperature carryover, and the full start-to-completion horizon. Also report a fixed-clock-horizon comparison where useful; it answers a different question.

Design controlled synthetic cases to separate burst duration, peak current, energy, and harmonic spectrum. State which quantities are matched in each comparison; not all can be independently held constant. Include short bursts and sustained plateaus to test whether oil thermal inertia filters the fast variations.

When downsampling, retain interval-average loss inputs, especially mean squared current:

$$
\langle I^2\rangle \ne \langle I\rangle^2.
$$

Compare coarse results with a resolved baseline before adopting a production timestep. Preserve harmonic-weighted loss contributions as well as resistive heating. Ramp rate alone is not an aging metric.

### 7.2 Harmonics and electrical topology

Compare sinusoidal and measured representative harmonic spectra at matched RMS current to isolate harmonic-loss effects. Compare realistic UPS modes and load-dependent spectra separately. A harmonic mitigation scenario should use a feasible spectrum, not simply set all harmonic losses to zero.

Evaluate BESS both upstream and downstream of the studied transformer where topology allows. Only transformers upstream of the point of battery power injection necessarily see the reduced net demand. Include recharge, conversion losses, inverter limits, state of charge, and reserve requirements when comparing energy and thermal benefits.

### 7.3 Environment, capacity, and cooling

Compare seasonal ambient sequences, loading headroom, transformer sharing, cooling control, degraded cooling, and credible N-1 operation. Apply asset-specific loading and operating limits; life optimization must not bypass protection or abnormal-condition constraints.

Use representative operating days and seasonal sequences to form annual exposure scenarios. Do not repeatedly extrapolate a single worst two-hour trace as a realistic annual operating history. Distinguish a stress case from an expected-operation forecast.

### 7.4 Accelerated oil-aging experiments

Use a staged design rather than a large full factorial immediately:

1. Establish fresh-oil and aged-oil baselines and test-method repeatability.
2. Select at least three defensible temperature levels after confirming safety and likely mechanism consistency.
3. Compare controlled low/high oxygen availability and relevant inhibited/uninhibited or inhibitor-depleted conditions.
4. Include oil-only controls and oil-paper-copper assemblies; control material ratios, vessel headspace, seals, and moisture.
5. Use independent vessel replicates and time-series measurements. Repeated samples from one vessel are not independent replicates; account for sample withdrawal and air exposure.
6. Reserve a temperature or exposure condition for out-of-sample validation and include a lower-temperature confirmation case where feasible.

Measure inhibitor, acidity, interfacial tension, moisture, dielectric loss/resistivity, and selected oxidation products. Add DGA, sludge evidence, and paper-condition measurements where they answer a specific mechanism question.

Choose sampling intervals from pilot kinetics so that the induction period, inhibitor depletion, and later oxidation are resolved. Estimate the required replicate count using pilot variability and the smallest meaningful effect, not an arbitrary large test matrix.

Fit kinetic parameters across temperatures, inspect residuals and mechanism changes, and test whether a single Arrhenius slope is defensible. Accelerated aging is a calibration experiment, not proof that one laboratory week equals a fixed number of field years. Follow laboratory chemical, electrical, and pressure-safety procedures.

### 7.5 Field study

Begin with baseline condition tests and continuous electrical/thermal monitoring. Use consistent laboratory methods and clean sampling practice. Sample periodically at an asset-appropriate interval, with additional samples after significant overheating or maintenance where indicated.

Seek comparable assets with different loading/scheduling exposure, while recording age, preservation system, oil formulation, cooling, moisture, and treatment. Analyze matched or longitudinal comparisons rather than attributing every difference to workload shaping.

Several months may characterize seasonal temperatures and early condition trends; establishing oil-service intervals can require years. If degradation is too slow to resolve, report the limit of detection and rely on calibrated exposure comparisons instead of forcing a trend.

## 8. Validation and uncertainty

### 8.1 Validation gates

| Gate | Checks | Acceptance principle |
| --- | --- | --- |
| Input integrity | Units, timebase, topology, RMS/harmonic normalization, missing data | No unresolved ambiguity that changes loading or exposure materially |
| Electrical losses | Sinusoidal baseline, rated-loss consistency, harmonic cases | Recover known reference cases without double-counting losses |
| Thermal behavior | Steady state, load steps, ambient changes, cooling transitions, warm starts | Agreement within a pre-agreed tolerance based on sensor/OEM uncertainty |
| Numerical behavior | Timestep refinement, integration units, event handling | Numerical error substantially smaller than the effect being compared |
| Paper aging | Reference-temperature aging factor, analytic constant-temperature cases | Correct reference behavior and consistent integration |
| Oil chemistry | Nonnegative states, plausible inhibitor/oxygen behavior, held-out conditions | Parameters identifiable and prediction errors characterized |
| Intervention behavior | Drying, oil additions, reclamation, reinhibition | State changes reflect measurements and do not reset paper age |
| Forecasting | Withheld assets/windows and future-condition scenarios | Calibrated uncertainty; conclusions limited to the validated domain |

Set numerical tolerances before evaluating strategy benefits. As a provisional numerical target, timestep refinement should change equivalent aging by less than 1%, and by less than the relevant strategy effect; tighten this if the effect is small. This is a project target, not a standard requirement.

Thermal error tolerances must reflect instrumentation and use case. A model adequate for ranking broad loading scenarios may be inadequate for deciding operation near a temperature limit.

### 8.2 Calibration procedure

1. Calibrate losses and thermal behavior before fitting chemistry; do not allow chemistry parameters to compensate for temperature-model errors.
2. Fix or bound poorly identifiable parameters using independent measurements.
3. Fit a minimal chemistry model and test held-out temperatures or conditions.
4. Test transfer to field observations, updating asset-specific initial states where justified.
5. Escalate model complexity only if residuals identify a missing mechanism and the new parameters can be constrained.

Avoid randomly splitting adjacent samples from the same run into training and validation. Hold out whole vessels, temperatures, time blocks, or assets as appropriate. Report extrapolation separately from interpolation.

### 8.3 Uncertainty and forecast interpretation

Propagate measurement, parameter, model-structure, threshold, and future-operation uncertainty. Use sensitivity analysis first, then Monte Carlo or a justified probabilistic parameter model. Preserve correlated uncertainties rather than treating all inputs as independent by default.

Report distributions or intervals for maintenance-threshold crossing, conditional on scenario assumptions. Also report the fraction of scenarios with no crossing within the forecast horizon. Identify the uncertainty sources that most affect the decision and the measurements that would reduce them.

Use counterexamples deliberately: a strategy can reduce ramps while increasing energy, running time, or sustained temperature. A strategy benefit should survive reasonable uncertainty and adverse operating scenarios before being used operationally.

## 9. Proposed implementation sequence

This sequence describes future work only. No Python modules, notebooks, dependencies, or operational controls are introduced by this document.

| Phase | Work | Deliverable | Gate to proceed |
| --- | --- | --- | --- |
| 0: Scope and evidence | Select asset/fluid; inventory data; choose thresholds and standards; document topology | Parameter/source register and measurement plan | Inputs and assumptions approved |
| 1: Thermal screening | Evaluate reference implementations; build/reuse loss and thermal solver; replay workloads | Temperatures, losses, paper exposure, sensitivity plots | Thermal and numerical validation pass |
| 2: Oil kinetics | Run pilot tests or acquire field histories; fit minimal chemistry | Calibrated parameters, observation relationships, held-out results | Chemistry evidence supports forecast scope |
| 3: Coupled forecasting | Add moisture exchange where supported, interventions, and future-operation ensembles | Conditional maintenance forecasts with uncertainty | Field-transfer limitations documented |
| 4: Operational comparison | Compare scheduling, BESS, cooling, headroom, and treatment | Cost/performance/condition tradeoff report | Benefit exceeds uncertainty and respects limits |
| 5: Optional integration | Add validated constraints or penalties to workload scheduling | Offline-tested control proposal | Forward model and operational safeguards accepted |

Conditional effort estimate: a thermal prototype and workload comparison may take roughly 2-4 engineering weeks once asset data are available. Chemistry modeling requires additional effort and is gated by laboratory/field evidence; laboratory work may take weeks to months, and field validation may take months to years. These are planning ranges, not commitments.

Keep transformer modeling separate from turbine-specific internals, with a thin adapter from existing workload outputs. Reuse current trace loaders, plotting conventions, tests, and Pixi workflows where suitable. Do not bind transformer chemistry to a turbine governor model or require detailed electrical transient simulation for ordinary long-horizon thermal exposure.

Evaluate the [IEEE-linked open-source thermal model project](https://opensource.ieee.org/inslife/ieee-c57.91-thermal-models) before writing a custom implementation. Inspect model applicability, interface, tests, and licensing; a public project link alone does not establish fitness or compliance.

## 10. Outputs and decision criteria

Each scenario report should include:

- Asset/fluid, parameter sources, topology, model version, validity range, and initial condition.
- Work completed, scheduling delay, makespan, energy, peak RMS current, and harmonic loss assumptions.
- Top-oil/hot-spot temperature profiles, peak and sustained exposure, cooling-mode activity, and losses.
- Equivalent paper-aging hours and clearly labeled reference-life calculations where applicable.
- Oil-indicator trajectories, inhibitor depletion, moisture/relative saturation, and threshold-crossing intervals where calibrated.
- Maintenance actions and costs, including fan/pump energy and BESS conversion/recharge effects.
- Uncertainty intervals, sensitivity ranking, withheld-validation results, and claims not supported by the available data.

Prefer the least complex intervention that produces a measurable benefit while meeting temperature, electrical, workload, and reliability constraints. Do not assign an economic value to unvalidated years of life extension. Compare cost per completed work and expected maintenance/aging outcomes only within the model's evidence-supported scope.

### Recommended first study

**Question:** Do workload shaping and power buffering measurably reduce transformer thermal exposure, and under what fluid and preservation conditions could that reduce oil deterioration?

Begin with the existing scheduler strategies, a calibrated transformer thermal model, realistic transformer-side current mapping, and separate harmonic scenarios. Report thermal and paper-aging comparisons immediately. Add oil-life forecasts only after chemistry calibration.

A finding that second-scale smoothing has little oil-life benefit is a valid result: it may still help turbine dynamics, while transformer oil maintenance is driven more strongly by sustained loading, cooling, oxygen exposure, or moisture.

## 11. Risks, pros, and cons of the recommended approach

| Aspect | Benefit | Limitation and mitigation |
| --- | --- | --- |
| Separate paper and oil models | Avoids misleading lifetime claims and supports appropriate maintenance | More outputs to interpret; keep definitions and decision thresholds explicit |
| Reduced-order thermal model first | Fast workload studies and straightforward calibration | Limited spatial detail; escalate only after targeted validation failure |
| Minimal chemistry model | Easier parameter identification and transparent uncertainty | May miss later-stage mechanisms; extend using residuals and additional evidence |
| Laboratory plus field evidence | Balances controlled experiments with operating relevance | Longer program; use staged gates and publish screening results separately |
| Explicit intervention modeling | Enables realistic maintenance and cost comparisons | Treatment effectiveness varies; use measured pre/post condition |
| Probabilistic forecasts | Shows decision robustness rather than false precision | Requires credible uncertainty assumptions and held-out checks |
| Scheduler integration last | Avoids optimizing against an unvalidated life proxy | Slower path to deployment; offline comparisons provide early research value |

## 12. Standards and references

1. [IEEE C57.91-2025: Guide for Loading Mineral-Oil-Immersed Transformers and Step-Voltage Regulators](https://standards.ieee.org/ieee/C57.91/7163). Public scope, revision status, and reference-model link checked on 2026-10-07. IEEE lists publication on 2026-03-05 despite the 2025 designation.
2. [IEEE C57.110-2018: Recommended Practice for Establishing Transformer Capability when Supplying Nonsinusoidal Load Currents](https://standards.ieee.org/standard/C57_110-2018.html). Public scope checked; verify applicability and full calculation procedures before implementation.
3. IEC 60076-7: Loading guide for mineral-oil-immersed power transformers. Alternative/complementary thermal and aging guidance; select an applicable edition and do not mix parameter conventions silently.
4. IEC 60422: Mineral insulating oils in electrical equipment - supervision and maintenance guidance. Basis for asset-appropriate condition interpretation and intervention decisions; verify the current applicable edition.
5. IEC 60599 and IEEE C57.104: dissolved-gas interpretation guidance. Use for diagnostic context, not as standalone oxidation-lifetime models.
6. [Existing scheduler study](scheduler_ramp_demo.md), [workload data documentation](../data/README.md), and [electrical transient modeling context](electrical_transient_modeling.md).

Public IEEE pages establish scope and status, not verification of every equation in this plan. IEC editions, detailed standard clauses, test methods, numerical thresholds, and OEM limitations remain implementation prerequisites. Candidate equations above are explicitly subject to those checks and asset-specific calibration.
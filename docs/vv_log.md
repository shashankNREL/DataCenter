# V&V Log — Governor (PES-TR1) and Gas Plant Implementations

Living document. Records (1) the full critique that motivated the V&V campaign,
(2) a running change log for every phase, and (3) deviations from the approved plan.

- Started: 2026-07-18
- Scope: `gas_plant/`, `gas_plant/dynamics/`, `gas_plant_andes/`
- Companion report: `docs/vv_report.tex` (equations + results, novice-readable)

---

# Part 1 — Critique (2026-07-18, pre-fix snapshot)

Verbatim copy of the review that motivated this campaign. File/line references
are to the pre-fix code (commit `71af915`).

## Overall verdict

The engineering discipline is above average for a research repo: `docs/tier_plan.md`
logs parameter provenance, deviations, and documents its own failed attempts. The
PES-TR1 Appendix C transcription in `papers/pes-tr1-2013-appendixC.txt` matches the
defaults in `GGOV1Params` line for line (verified value by value).

But it is **not yet defensible in front of a grid-dynamics audience**. There is one
systemic per-unit-base error that invalidates three separate claims the docs make, a
fuel-accounting bug that silently biases every Tier B/C fuel number, a latently wrong
sign on `Dm`, an LM9000 part-load efficiency curve that is flatly unphysical, and zero
automated tests on any of the dynamics code (the promised `tests/test_dynamics.py`
was never written — all validation lives in notebooks).

## 1. Governor (PES-TR1 GGOV1 / TGOV1) — serious findings

### G1 — CONFIRMED: the "VMAX rebase" (A4) does not do what the docs claim in GGOV1, because valve ≠ power once Kturb ≠ 1

In Tier A TGOV1, valve pu ≈ power pu, so `Vmax = 22/23` genuinely caps Pm at 22 MW.
Tier B ported that number (`ggov1.py:97`) into a model where `Pm = Kturb·(Wf − Wfnl)`
with `Kturb = 1.5`, `Wfnl = 0.2`. Consequences:

- **Max mechanical power at valve limit** = 1.5 × (0.957 − 0.2) = **1.135 pu = 26.1 MW**,
  not the claimed 22 MW. A4's rationale is dead on arrival in Tier B/C.
- **The temperature limiter caps at the wrong power.** `tlim = Ldref/Kturb + Wfnl` with
  `Ldref = 1.0` limits Pm at 1.0 pu *on the generator base* = **23 MW**, not 22 MW. The
  docs say Ldref is "pu turbine MW base" — it is applied on gen base. Two inconsistent
  thermal caps (26.1 MW valve, 23 MW temp), neither of which is the documented 22 MW.
- **The initial-load guard is wrong**: `ggov1.py:429` checks `Pe0 > Vmax·Kturb·1.05`
  = 1.507 pu = **34.7 MW** — it forgot the `−Kturb·Wfnl` term. The correct steady-state
  ceiling is 1.135 pu. The model can be initialized 50% above physical capability.
- **Vmin semantics flipped between tiers.** Tier A: `Vmin = 0.15` means 3.45 MW minimum
  power ("lean blowout floor"). Tier B: the same 0.15 is a fuel-stroke floor *below*
  `Wfnl = 0.2`, i.e. Pm_min = 1.5 × (0.15 − 0.2) = **−0.075 pu** (negative power — actually
  physical for a GT below no-load fuel, and PES-TR1's own table has VMIN 0.15 / WFNL 0.2 —
  but the docs still describe it with the Tier-A "15% of rated fuel" story, now false).

**Root cause:** PES-TR1 §3.3 puts GGOV1 on the *turbine MW base* and handles the gen-base
conversion at the machine interface (PSS/E's `Trate`). The repo instead put everything on
gen base and hand-edited one limit. The fix is structural: run the governor on turbine base
with an explicit 22/23 conversion at the swing-equation boundary; set `Vmax = 1.0` (stroke
limit, not a power limit) and enforce the thermal cap through `Ldref`.

### G2 — CONFIRMED: Tier B/C fuel accounting feeds a *fuel-valve* signal into a *power-load* axis

`ggov1.py:533` and `multishaft.py:413`: `load_frac = P_fuel·Sn/P_turbine_mw`, where
`P_fuel` tracks the valve. In GGOV1, valve = fuel stroke where 0.2 = zero power and 0.867 =
full load. `dispatch_fn` expects a *load fraction* (power). At Pm = 0.9 pu the surrogate is
evaluated at ~0.84 load; at Pm = 0.5 pu, at ~0.56 load. Worse, this **double-counts no-load
fuel**: the valve signal already embeds `Wfnl`, and the surrogate table independently embeds
its own idle fuel (4.58/12.1 = 38% of full-load fuel at zero load).

The tier plan's claim that "Tier B fuel is 5% lower because Kturb explicitly models no-load
losses" is an artifact of this mapping, not physics. Every Tier B/C fuel and CO₂ total is
biased by it.

### G3 — CONFIRMED (latent): `Dm` sign is anti-damping

`ggov1.py:329`: `Pmech = Kturb·(x_turb_out − Wfnl) + Dm·(ω − 1)`. In GGOV1, positive `Dm`
*reduces* mechanical power as speed rises (damping). As written, `Dm > 0` would inject energy
on overspeed — destabilizing. Harmless today only because the default is 0. Same code copied
into `multishaft.py:240`.

### G4 — Structural deviations from the standard models

- **TGOV1 limiter placement + extra pole.** Standard TGOV1 clamps the T1-lag state (valve),
  *then* applies (1+sT2)/(1+sT3). The repo applies a (1+sT2)/(1+sT1) lead-lag, clamps its
  output, then adds a rate-limit "chase" with time constant **T1 again** (`tgov1.py:232`).
  In the default mode the linearized TF is (1+sT2)/[(1+sT1)²(1+sT3)] — an extra pole at 1/T1
  that standard TGOV1 does not have.
- **`rselect` is a lie.** The parameter documents four modes; `_compute_controllers`
  hardcodes rselect=1. Isochronous (0) — the mode an islanded single unit would actually
  run — is silently ignored.
- **Omitted GGOV1 blocks**: Kdgov/Tdgov, deadband `db`, KIMW/Pmwset, Rup/Rdown, Teng.
  Zero-by-default in PES-TR1 so acceptable — but it's a subset implementation and should say so.
- **Back-calculation LVG anti-windup is nonstandard.** Defensible continuous relaxation of
  PES-TR1 switched tracking, but `Kbc = 100` is an invented stiffness knob with no
  sensitivity study.
- **Temperature path uses Pmech, not Wf** (`tex = Pmech/Kturb + Wfnl`). Nearly equivalent,
  but a deviation with no note.
- **Diagnostics inconsistency:** the reported `Pe_mw` column is the raw ZOH demand, not the
  frequency-damped Pe used in the swing equation.

### G5 — Damping double-counting

`D_pu = 1.0` on the machine **plus** `alpha_load_damping = 1.5` on the load. Kundur's D of
1–2 *is* the load-frequency sensitivity; if load damping is modeled explicitly, machine D
should be ~0. As is, total effective damping ≈ 2.5 pu — every nadir in the tier tables is
optimistic.

### G6 — Unpinned constants that carry the whole result

- **H = 2.8 s** — flagged "unsourced" in the repo's own docs, never resolved. Nadir scales
  ~1/H. Tier C then invents a split (2.5/0.3), Tier E invents a further split (0.5/2.0).
- **Hannett validation protocol mismatch**: tier plan states "50→100% step," executed test
  is "50→60% step." The 1.30 s vs 1.14 s "close match" claim is not auditable until fixed.
- **Tier C gas-path coupling** `P_couple = K_couple(ω_hp − ω_idle)` is a linear invention;
  real FPT power vs NGG speed is strongly nonlinear (~cubic) and has ω_pt dependence.
  Spool time constant M_hp/K_couple ≈ 0.29 s vs real NGG spool response 0.5–1.5 s.
- `omega_hp_idle = 0.516` properly pinned to the Pocket Guide (good), but `delta_hp`
  integrates ω_hp−1 on the 377 rad/s electrical base and drifts secularly — unused, but
  an audit red flag.

## 2. Gas plant implementation

### P1 — CONFIRMED: LM9000 part-load efficiency is unphysical

`lm9000.py:200`: `eta(load) = −0.02(load−1)² + 0.3952` gives **39.0% at 50% load and 37.5%
at zero load**. Real aeroderivative part-load efficiency at 50% load is ~32–34% and collapses
toward idle. The repo's own ThermoPower surrogate says 30.9% at 50% and 16.6% at 20% — the two
gas plant models in the same package disagree by 6–20 efficiency points at part load. The
−0.02 coefficient is cited to nothing.

### P2 — "First principles / component-based" framing of lm9000.py is cosmetic

Compressor/turbine maps are decorative dataclasses the dispatch path never touches. Dispatch
is: linear power × invented quadratic efficiency × fixed exhaust T × AFR-proportional exhaust
flow. Physical problems: exhaust flow ∝ fuel at all loads (real machines hold airflow much
flatter — VSVs/VIGVs); fixed exhaust T contradicts fixed AFR thermodynamically; nobody has
closed `fuel·LHV = P + m_exh·cp·ΔT + losses` at any load. The LM9000 "datasheet"
(56.723 MW / 39.52% / 492.9 kg CO₂/MWh) is cited but not archived in the repo.

### P3 — The 235 MW ThermoPower surrogate is stretched across an order of magnitude

`unit.py` scales linearly with rated power, treats efficiency and exhaust T as size-invariant.
Tolerable within ±30% of 235 MW (F-class). The LM2500 notebooks use it at 22 MW — a 10.7×
extrapolation across engine classes. An F-class efficiency curve is not an LM2500.

### P4 — Combined cycle: mostly sound, two citation problems

Energy-balance structure and LM9000 CC auto-tune are good. But the claim that the linear ΔT
derate "is what PES-TR1 / CIGRE 238 use" (`lm9000.py:394`) is untraceable — CIGRE TB 238
models steam power as a lagged function of GT exhaust *energy*. `cp_gas` is 1100 in one
module, 1050 in the other, both "standard values." The heavy-frame CCPP still uses
`_size_scale` post-rescaling of fuel — the pattern the LM9000 fix declared nonsense (benign
here, but inconsistent).

### P5 — Constants that are fine

LHV 49 MJ/kg, CO₂/fuel 2.75 (stoich CH₄) and 2.65 (back-calculated), AFR ≈ 49 pinned to
NAVEDTRA — defensible as documented.

## 3. ANDES coupling (`gas_plant_andes/`)

- **MW ≠ MVA**: `case_builder.py:91` uses `rated_power_mw` as generator `Sn`. At PF
  0.85–0.9 the machine base (and hence effective inertia) is off 10–18%.
- **GENCLS + TGOV1 with VMAX = 1.2** allows 20% steady overload — contradicting the A4
  philosophy in the scipy path. `ExciterDefaults` is dead code (EXST1 never added; GENCLS
  can't take an exciter), so `voltage_dc_pu` is a constant-flux artifact.
- **Resync without a sync check**: tie recloses at t=15 s via bare Toggle regardless of
  angle/frequency difference. Out-of-phase reclose is a catastrophic event in reality.
- **Unverified base assumption**: `scenarios.py` asserts GENCLS.tm is on system MVA base and
  indexes BusFreq positionally — needs numeric assertions in a test, not comments.
- **Fuel join** re-runs `plant.dispatch` in a Python loop per timestep (inefficiency only).

## 4. Process / provenance

- **`docs/gt_dynamics_notes.md` is pasted LLM chat output** with unsourced numbers
  (0.5–1.5 s transport delay, 57.5 Hz trip, 25–30% block-load capability). Quarantine or
  rewrite with citations.
- **No CI-able dynamics tests.** `tests/smoke_test.py` covers steady-state surrogates only.
- **Environment**: smoke tests referenced a conda env; nothing pinned the ANDES version.
  (Resolved 2026-07-18: pixi environment added, andes 2.0.0 from conda-forge.)

---

# Part 2 — Approved V&V plan

- **Phase 0** — Fix confirmed defects: G1 per-unit base overhaul; G2 fuel mapping;
  G3 Dm sign + rselect + damped-Pe reporting + TGOV1 double-pole; P1 LM9000 part-load
  efficiency; G5 damping decision.
- **Phase 1** — Verification: ANDES cross-simulation of GGOV1/TGOV1 with identical
  parameters; small-signal checks (droop identity, eigenvalues, torsional frequency);
  invariant pytest suite.
- **Phase 2** — Constants defensibility dossier (`docs/constants.md`), statuses
  pinned / estimated / placeholder.
- **Phase 3** — Literature validation: Hannett & Khan 1993 redo (protocol fix, documented
  H·Sn assumption); Rowen model cross-comparison; thermo energy-balance closure; CO₂/LHV
  cross-check; ANDES path upgrade (GENROU + GGOV1 + EXST1, Sn = MW/0.85, sync caveat,
  base-assumption tests).
- **Phase 4** — Regression harness: tier numbers → `tests/test_dynamics.py` with
  tolerances, run under pixi.
- **Report** — `docs/vv_report.tex`: all equations + V&V results, novice-readable.

---

# Part 3 — Running change log

## 2026-07-18 — Environment (pre-phase)

- Added `pixi.toml` / `pixi.lock`: python 3.12, numpy/pandas/scipy/matplotlib, pytest,
  jupyterlab, **andes 2.0.0 (conda-forge — PyPI kvxopt fails to build on macOS without
  SuiteSparse headers)**; `gas_plant` installed as editable local package.
- Tasks: `pixi run smoke`, `pixi run lab`.
- `environment.yml` now redundant (kept for reference).

## Decisions taken without further user input (flagged for review)

- **D1 (droop vs isochronous):** user question outstanding. Implemented *all* rselect modes
  (0 isochronous, 1 electrical power, −1 valve stroke, −2 governor output); default remains
  `rselect = 1` (droop) to preserve continuity with prior tier results. Isochronous is
  exercised in the verification suite.
- **D2 (fuel anchor):** GGOV1-native fuel calibration anchored to LM2500 gen-set design
  efficiency `eta_design = 0.365` (GE LM2500 gen-set ISO efficiency ≈ 35–38% depending on
  variant; midpoint, status **estimated**). Cross-check documented against NAVEDTRA
  442,800 lb/h air / 9,000 lb/h fuel (= 1.134 kg/s → 35.3% at 19.6 MW *shaft*).
- **D3 (LM9000 part-load form):** Willans line (affine fuel vs load, no-load fuel fraction
  0.2, consistent with GGOV1 `Wfnl`), giving eta(L) = eta_fl·L/(0.2 + 0.8·L). Standard,
  citable form; replaces the unphysical quadratic.
- **D4 (TGOV1 legacy status):** TGOV1 (Tier A) restructured to the standard block layout and
  kept on generator base as a *legacy* model; GGOV1 is the reference governor going forward.
  Tier-0 bit-compatibility flags are removed (historical comparisons live in the notebooks).

## 2026-07-18 — Phase 0: confirmed-defect fixes (COMPLETE)

### G1 — Per-unit base overhaul (`gas_plant/dynamics/ggov1.py`, rewritten)

- GGOV1 now runs on the **turbine MW base** `Trate_mw` (PES-TR1 §3.3); the swing
  equation stays on the generator base; single conversion `kb = Trate/Sn` at the
  Pm and Pe interfaces (PSS/E `Trate` pattern).
- `Vmax_pu = 1.0` — a valve **stroke** limit (PES-TR1 Appendix C value restored);
  the former hand-edited 22/23 stroke limit is gone.
- Thermal cap enforced by the temperature limiter at `Pm = Ldref = 1.0 pu turbine
  = 22.0 MW` (verified numerically: 15→25 MW demand step settles at Pm = 22.000 MW).
- Transient ceiling exposed as `Pm_transient_max_pu = Kturb*(Vmax − Wfnl) = 1.2 pu
  = 26.4 MW` (short-term capability until the temp limiter winds fuel back) —
  now an explicit, documented property instead of an accident.
- Initial-load guard corrected: refuses `Pe0 > Ldref` (22 MW), replacing the wrong
  `Vmax*Kturb*1.05` bound (which allowed 34.7 MW).
- Propagated to `multishaft.py` (HP-rotor swing entirely on turbine base;
  PT/gen swing on gen base with `kb*P_couple`).

### G2 — Fuel accounting (`ggov1.py`, `multishaft.py`)

- Native fuel path: `fuel_kg_s = wf_base_kg_s * Wf` with `wf_base_kg_s` calibrated
  so rated output burns `Trate/(eta_design*LHV)`. Defaults: `eta_design = 0.365`
  (status ESTIMATED, see constants.md), LHV 49 MJ/kg → 1.419 kg/s per pu Wf,
  1.379 kg/s at rated (cross-check: NAVEDTRA 9,000 lb/h = 1.134 kg/s at 19.6 MW
  shaft = 35.3 % shaft efficiency; our 22 MW electrical anchor gives the same
  ballpark).
- `dispatch_fn` path corrected: the fuel signal is converted to an equivalent
  POWER load fraction `Kturb*(Wf − Wfnl)` before calling the surrogate (pre-fix
  code passed raw valve stroke as load fraction → double-counted no-load fuel).
- `P_fuel` combustor-lag state now tracks `Wf` (fuel flow incl. speed factor),
  not the bare valve stroke.

### G3 — Dm semantics (`ggov1.py::_turbine_power`)

- `Dm > 0`: `Pm -= Dm*(ω−1)` (damping — sign now per PES-TR1/PSS/E; pre-fix
  code ADDED the term, i.e. anti-damping).
- `Dm < 0`: fuel-flow speed sensitivity `Wf *= ω**Dm` (newly implemented).
- Verified: 18→5 MW rejection overspeed peak 75.07 Hz (Dm=0) vs 72.03 Hz
  (Dm=0.5) — damping acts in the correct direction.

### G4 — Structural conformance

- `rselect` honored: 1 (electrical power), 0 (isochronous — verified f returns
  to exactly 60.000 Hz after a step), −1 (valve stroke), −2 (governor output,
  algebraic loop solved in closed form). Driver picks the matching `Pref`.
- Temperature-path input is now the fuel flow `Wf` (standard), not the
  back-derived `Pmech/Kturb + Wfnl`.
- `Pe_mw` in all results is the ACTUAL damped electrical power;
  `Pe_demand_mw` added for the raw ZOH demand.
- TGOV1 (`tgov1.py`, rewritten) now has the standard block layout: droop input
  → 1/(1+sT1) with non-windup VMIN/VMAX on the valve state → (1+sT2)/(1+sT3)
  → Pm − Dt·Δω. The spurious extra T1 pole (rate-limit "chase") is gone; `Dt`
  parameter added; state vector reduced 6 → 5 (Pm is algebraic).
- Explicitly documented as a SUBSET implementation: Kdgov/Tdgov, db,
  KIMW/Pmwset, Rup/Rdown, Teng not implemented (all inactive in PES-TR1
  Appendix C defaults).

### G5 — Damping decision

- `D_pu` default 0.0 in TGOV1, GGOV1, and multishaft (`D_pt`); load-frequency
  sensitivity carried solely by `alpha_load_damping = 1.5` (Kundur §11.1.4).
  Pre-fix combination (D=1 + α=1.5) double-counted damping → optimistic nadirs.

### P1 — LM9000 part-load efficiency (`gas_plant/lm9000.py`)

- Quadratic `−0.02(L−1)² + 0.3952` replaced with the **Willans line**:
  `eta(L) = eta_fl·L/(b + (1−b)L)`, `b = no_load_fuel_frac = 0.2` (consistent
  with GGOV1 Wfnl). Now: 22.0 % @ 20 % load, 32.9 % @ 50 %, 39.52 % @ 100 %.
- LM9000 CC auto-tune unaffected at design (50.49 % full load); part-load CC
  efficiency now physical (45.8 % @ 60 %, 32.9 % @ 20 %).
- `tests/smoke_test.py` part-load assertion updated (old one encoded the
  unphysical flat curve).

### P4 — Citation retraction (`lm9000.py`)

- The claim that the linear ΔT bottoming-efficiency derate "is what PES-TR1 /
  CIGRE 238 use" was wrong and is retracted in-code; the form is now labeled a
  pragmatic fit.

### Provenance

- `docs/gt_dynamics_notes.md` quarantined with a provenance warning header
  (unedited LLM chat output; not citable; kept as ideas memo only).

### Deviations from plan discovered during Phase 0

- **D5 (TGOV1 rate limit removed by default):** placing the Tier-A ±0.1 pu/s
  slew limit at the STANDARD limiter location (inside the single-lag droop
  loop) produces a sustained ±1.3 Hz limit cycle at LM2500 gains (R=0.04,
  T1=0.15) — a slew-induced describing-function instability, not a numerical
  artifact. The pre-fix code avoided it only because its rate limiter sat
  OUTSIDE the feedback path (nonstandard). Standard TGOV1 has no rate limit;
  rate-limited fuel dynamics are correctly represented in GGOV1 (MaxERR clamp
  bounds the commanded rate). `use_valve_rate_limit` now defaults to False and
  carries a warning docstring. Consequence: Tier-A tier-table numbers that
  depended on the old rate-limit placement are superseded.
- **Behavioral changes accepted (results will differ from tier_plan tables):**
  D=0 (deeper nadirs than pre-fix), corrected fuel mapping (different fuel
  totals), Vmax=1.0 stroke + Ldref cap (different saturation behavior),
  TGOV1 restructure (no extra pole). The tier_plan tables are historical;
  Phase 4 establishes new regression baselines.

## 2026-07-18 — Phase 1: verification (COMPLETE)

### ANDES cross-simulation (`tools/vv/crosscheck_andes_tgov1.py`)

- **Deviation from plan:** ANDES 2.0 has **no GGOV1 model** (governors: TG2,
  TGOV1(+DB/N/NDB), GAST, HYGOV family). The end-to-end cross-simulation
  therefore runs on TGOV1 (verifying the swing equation + a standard governor
  implementation against an independent tool); GGOV1 is verified by numerical
  linearization + closed-form invariants instead (below).
- Scenario: single machine (GENCLS, Sn=23, H=2.8, D=0) + TGOV1 (R=0.04,
  T1/T2/T3 = 0.15/0.3/1.5) + constant-P load, 15→18 MW step at t=1 s, 30 s.
- **Result: max |Δω| = 0.124 mHz; max |ΔPm| = 8.6e-5 pu; nadirs and final
  frequency identical to 4 decimals. PASS** (gates: 1 mHz, 1e-3 pu).
- Findings needed to reach agreement (all documented in the script):
  1. A single-bus ANDES case leaves GENCLS.omega frozen (degenerate network) —
     a 2-bus case with a line is required.
  2. The comparison line must be lossless (r=0): with r=0.001 the I²r loss
     increment (≈33 kW) appears as a 3.4 mHz steady-state offset.
  3. **ANDES GENCLS uses the speed-voltage approximation** (stator flux at
     ω=1): te = ψd·Iq − ψq·Id = Pe for ra=0, and TurbineGov adds pout−tm0 to
     the tm equation (tm = pout one-to-one). ANDES's effective swing equation
     is therefore the **power form** — verified against GENBase/GENCLS source.
     `TGOV1Params.torque_form` added to expose the strict torque form; the
     cross-check runs power form.
  4. Empirically pinned: `GENCLS.tm/te` are on the SYSTEM MVA base (0.1507 pu
     on 100 MVA for the 15 MW dispatch) — the assumption `scenarios.py` made
     without proof is now verified.

### Small-signal verification (`tools/vv/smallsignal.py`) — all PASS

- IC equilibrium: ‖RHS‖ < 3e-15 at 5, 11.5, 15, 20, 21.9 MW.
- Numerical linearization (12×12 Jacobian): exactly one structural zero mode
  (rotor angle; no feedback in islanded constant-P case), all other
  eigenvalues Re < −1e-3 at every operating point (slowest mode −0.2 /s).
- Droop DC identity (rselect=1): measured R = 0.04000 vs nominal (0.000 %).
- Isochronous (rselect=0): final |Δf| = 0.0000 mHz after a load step.
- Torsional closed form: ω_n = sqrt(ω₀·k_pu/I_red) → 22.0000 Hz vs 22 design.

### Invariant test suite (`tests/test_dynamics.py`, 30 tests, all pass)

GGOV1: IC equilibrium, linearized stability, 300-s hold < 0.1 mHz drift,
droop identity < 1 %, isochronous return to 60.000 Hz, thermal cap 22 MW
(temp limiter selected at end), transient ceiling 26.4 MW, initial-load
guard raises, Dm damping direction, Kbc 25× insensitivity (2.1 mHz), fuel
calibration at rated, all four rselect modes hold steady state.
TGOV1: droop identity, VMAX non-windup cap, steady hold.
Multishaft: IC equilibrium, HP-rotor energy balance (∫(Pm−P_couple)dt =
M·Δω to 1e-3), NGG speed↔load map, Tier B/C fuel agreement.
Torsional: natural frequency + damping ratio closed-form identities.
LM9000: Willans values, fuel affine in load, first-law bound, CC design point.
Plus 5 regression pins (Phase 4 baselines, see below).

### Additional fix found by the tests

- **Multishaft `delta_hp` state removed** (13 states now): it integrated
  ω_hp−1 on the 377 rad/s electrical base and drifted secularly at any part
  load (flagged in the critique; the IC-equilibrium test caught it with a
  residual of 58 rad/s). The HP rotor has no meaningful electrical angle.
- TGOV1 valve non-windup limiter: RK45 stages can overshoot VMAX by O(1e-4);
  test gate set accordingly (integration-scale, not a logic defect).

### pixi tasks added

`pixi run test`, `pixi run vv-crosscheck`, `pixi run vv-smallsignal`.

## 2026-07-18 — Phase 2: constants dossier (COMPLETE)

- Created `docs/constants.md`: every constant in `gas_plant/`, `gas_plant/dynamics/`,
  and `gas_plant_andes/` classified **PINNED / ESTIMATED / PLACEHOLDER** with a
  citation or derivation per row, plus a prioritized action list to harden it.
- Headline statuses: all GGOV1 block parameters PINNED to PES-TR1 Appendix C
  (transcription verified); **H = 2.8 s remains the highest-priority PLACEHOLDER**
  (nadir ∝ 1/H — sensitivity to be quoted in the report); Tier C gas-path
  coupling and the entire Tier E torsional/fatigue parameter set are
  PLACEHOLDER; fuel anchor eta_design = 0.365 ESTIMATED with NAVEDTRA
  cross-check; LM9000 datasheet numbers ESTIMATED until the PDF is archived.

## 2026-07-18 — Phase 3: literature validation (COMPLETE)

### Hannett & Khan (1993) — `tools/vv/validate_hannett.py` (PASS)

- **Protocol correction:** read the paper directly (pypdf added to the env).
  Table 3's protocol is "initial load = 50 % of generator MVA, step increase
  of 10 %, droop set to 3 %" — the tier_plan claim of a "50→100 % step" was
  wrong (the previously *executed* 50→60 % test was actually right).
- **Part A results** (T60 = time for Pm to reach 0.6 pu; anchors: Hannett
  typical model 1.140 s / field-derived 2.320 s):
  | model | T60 (s) | Δω_min (pu) |
  |---|---|---|
  | GGOV1 PES-TR1 typical (R=0.03) | **1.125** | −0.0127 |
  | GGOV1 LM2500 overrides | 0.950 | −0.0098 |
  | Rowen typical-gas | 0.655 | −0.0083 |
  The GGOV1 implementation reproduces the literature typical-model response
  to 1.3 %. Speed-excursion column is H-dependent (H not published per unit)
  — reported, not gated. The 2.32 s field-derived value remains the
  documented typical-vs-real bias (the paper's own core finding: typical
  models are ~40 % optimistic).
- **Part B (Beluga 5 6 MW load-rejection overlay, Figs 4/5):** replaced the
  old "80× magnitude mismatch" with a defensible reconstruction:
  - Turbine base **pinned from the data itself**: Vce₀ = 0.0965 pu and the
    paper's statement "Vce pu = Pm pu on turbine base" ⇒ base = 62.2 MW
    (Frame-7 class, consistent with the Beluga fleet).
  - Event time detected in the digitized record (t₀ = 1.96 s; the Vce spike
    to 0.135 at that instant treated as a breaker-transient artifact).
  - Rowen model with field-derived unit-2 dynamic constants (governor lag
    y = 3.05 s — the paper's key "real units are slow" finding); droop and
    H feature-fit (droop→settle, H→peak): droop = 0.046, H = 12 s
    (degenerate with the unpublished governor lag; documented).
  - **Untuned predictions: Vce min −0.0451 vs −0.0504 (11 %), Vce settle
    (29 %), t_peak (23 %); RMS Δω misfit 1.1e-3 pu.** Overlay figure:
    `docs/figs/hannett_overlay.png`.
- **New module:** `gas_plant/dynamics/rowen.py` — Rowen/Hannett Fig-1/2
  model (governor W(Xs+1)/(Ys+Z), K3/K6 fuel split, f2 torque law), used for
  cross-model comparison. Temperature loop omitted (paper confirms it stays
  on its max limit during these tests); ECR folded into tf; vendored
  "Typical Gas" cf2 = 1.5 treated as transcription artifact (Rowen 1983 and
  all unit rows use 0.5; steady-state identity bf2·K3 ≈ 1, af2 + bf2·K6 ≈ 0
  holds with 0.5).

### Thermodynamic validation — `tools/vv/thermo_validation.py` (PASS)

- First-law closure with cp bracketed [1005, 1150] J/kg/K:
  - Frame GT 235 MW: residual −5.7..+2.6 % at full load (closure brackets
    zero given cp uncertainty); +17..+26 % at 20 % load (surrogate idle-fuel
    dominated). No first-law violation at any load.
  - LM9000 SC (Willans): residual +7.7..+14.4 % at full load (gen/mech
    losses + the documented AFR-derived exhaust-flow underestimate); grows
    to +25..32 % at 20 % load — quantifies the documented fixed-AFR
    part-load limitation. First law never violated.
- CC efficiency curves monotone and physical: heavy CCPP 26.1→57.1 %,
  LM9000 CC 32.9→50.5 %.
- CO₂ factors: stoich CH₄ = 2.743; EPA Table C-1 pipeline ≈ 2.625; repo
  values 2.75 (generic) and 2.65 (LM9000) both in range.
- Part-load heat-rate table generated for the report.

### ANDES coupling v2 — `gas_plant_andes/case_builder.py::build_islanded_case_v2`

- GENROU (6th-order round rotor, Kundur-class parameters) + **EXST1 actually
  attached** (v1 exciter defaults were dead code) + TGOV1.
- Sn = rated MW / 0.85 (v1 used MW as MVA).
- Governor VMAX = turbine MW on machine base (v1 allowed 20 % overload).
- **Reclose guard:** resync Toggle is omitted with a warning unless
  `allow_unsynchronized_reclose=True` — no sync-check relay is modeled and
  an out-of-phase reclose is physically catastrophic.
- **Deviation:** plan said GENROU + GGOV1; ANDES 2.0 has no GGOV1, so TGOV1
  is used on the ANDES side (scipy GGOV1 remains the reference governor).
- `tests/test_andes_coupling.py` (5 tests) pins the interface assumptions
  numerically: tm/te on SYSTEM base (initialized at TDS init, not PFlow);
  governor pout enters tm one-to-one; **ANDES converts governor power limits
  to system base at setup** (VMAX.v = VMAX·Sn/mva_sys — newly discovered and
  pinned); BusFreq resolved by idx; v2 case islands and swings.

## 2026-07-18 — Phase 4: regression harness (COMPLETE)

- `tests/test_dynamics.py`: 31 tests (invariants + regression pins) and
  `tests/test_andes_coupling.py`: 5 tests — **36 total, all passing** under
  the pixi env in ~6 s.
- Post-fix regression baselines pinned (2026-07-18): GGOV1 11.5→13.8 MW step
  nadir 59.450 Hz / final 59.758 Hz; TGOV1 15→18 MW step nadir 59.300 /
  final 59.701 Hz; multishaft nadir 58.772 Hz; GGOV1 fuel at 15 MW
  0.9290 kg/s; **Load17 first-10-min replay: |Δf|max = 228.3 mHz, cumulative
  fuel = 640.1 kg** (Load17 = data/load17.csv, ×10 MW scaling as in the
  notebooks). The old tier_plan tables are superseded by these baselines.
- `gas_plant.dynamics` now exports the Rowen model; package docstring
  updated (GGOV1 marked as the reference governor).
- pixi task graph: `pixi run vv-all` = pytest (36) + smoke + ANDES
  cross-check + small-signal + Hannett + thermo. Single command, all PASS.

## 2026-07-18 — V&V report (COMPLETE)

- `docs/vv_report.tex` (compiled: `docs/vv_report.pdf`, 17 pages, tectonic).
  Novice-oriented: per-unit systems with a worked LM2500 example, swing
  equation from Newton's law, droop vs isochronous, full TGOV1/GGOV1 block
  equations with state and parameter tables (dossier statuses inline),
  back-calculation anti-windup derivation, steady-state initialization
  derivation, two-mass gas path with honesty box, torsional model incl. the
  ω₀ per-unit stiffness derivation and a rainflow explainer, Willans line,
  CC energy balance, Rowen/Hannett model, all Phase 1–3 results tables, the
  Hannett overlay figure, frozen regression baselines, constants summary,
  limitations, and the abridged defect log as an appendix.
- Reproduce everything: `pixi run vv-all`.

## 2026-07-18 — AI-workload notebook review (post-campaign)

- `notebooks/lm2500_ai_workload.ipynb` (MIT SuperCloud case) reviewed, fixed,
  and re-executed against the post-V&V models. Full findings in
  **`docs/ai_workload_review.md`**. Headlines: the notebook was not runnable
  as committed (foreign hardcoded path, stale `P_turbine_mw` API); the trace
  construction had three defects (slot-sum sampling-jitter sawtooth ~20 % on
  p99 slew — fixed to per-GPU ZOH; multiplicative PUE — fixed to additive
  baseload; unstated ×13,000 perfect-correlation scaling — now documented as
  a worst-case bound); and the original "years to failure ≈ 12,787"
  conclusion was derived from a post-trip, non-physical trajectory
  (44.8–67.3 Hz, NGG speed −0.48 pu). New notebook Sections 6a–6c add trip
  assessment against PRC-024-class thresholds, BESS sizing via low-pass
  power split with a verification re-run, and fatigue on the physically
  valid buffered case. Env: boto3 + pyarrow added.

## Final status

All phases complete. 36 automated tests + 4 validation tools, all passing.
Open user decisions: D1 (droop vs isochronous default for the islanded use
case) and the dossier's priority-action list (pin H, archive datasheets,
vendor governor retune, spool-trace calibration).

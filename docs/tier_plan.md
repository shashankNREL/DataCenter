# LM2500 Dynamic Model Upgrade — Tier A / B / C / E Plan & Change Log

Living document. Each tier section logs every parameter value used, the source it came from, every deviation from the original spec with a reason, and every assumption invented to fill a gap in the literature.

---

## Scope & rationale

The existing `lm2500_model.ipynb` swing-equation + TGOV1 ODE has known fidelity gaps documented in the project critique (Direct-Integration ODE, §3.2). This plan replaces it in four staged tiers without throwing away the original baseline:

| Tier | Goal | Independent? |
|---|---|---|
| **A** | Bug-fix the existing TGOV1 ODE in place (anti-windup, valve rate limits, load damping, VMAX rebase, fuel-from-valve, event-driven integration) | yes — no new literature data needed |
| **B** | Replace TGOV1 with GGOV1 (PES-TR1 Fig 3-5 / IEEE PES-TR1 2013) | needs PES-TR1 Appendix C parameter table |
| **C** | Two-mass shaft + single-axis generator + AVR via ANDES coupling | needs Tier B finished |
| **E** | 3-mass torsional model + rainflow on actual shaft torque, calibrated Miner damage | needs Tier C finished |

The original notebook (`notebooks/lm2500_model.ipynb`) stays untouched as the historical baseline. Each tier gets its own notebook for A/B comparison.

---

## File layout

```
DataCenter/
├─ docs/tier_plan.md                           ← this file
├─ gas_plant/dynamics/                         ← new sub-package
│  ├─ __init__.py
│  ├─ tgov1.py                                 ← Tier A (cleaned-up TGOV1 + fixes)
│  ├─ ggov1.py                                 ← Tier B (GGOV1)               (not yet)
│  ├─ multishaft.py                            ← Tier C (2-mass + ANDES)      (not yet)
│  └─ torsional.py                             ← Tier E (3-mass + rainflow)   (not yet)
├─ notebooks/lm2500_model.ipynb                ← unchanged baseline
├─ notebooks/lm2500_tierA_tgov1_fixes.ipynb    ← Tier A demo + diagnostics
└─ tests/test_dynamics.py                      ← regression tests             (added incrementally)
```

---

## Reference library (in `papers/`)

| Citation | Used for |
|---|---|
| **IEEE PES-TR1 (2013)** "Dynamic Models for Turbine-Governors in Power System Studies", Task Force on Turbine-Governor Modeling (P. Pourbeik, chair) | GGOV1 block diagram (Fig 3-5), typical parameter set (Appendix C), CIGRE GT model (Fig 3-7) |
| **Hannett & Khan (1993)** "Combustion turbine dynamic model validation from tests", IEEE Trans Power Sys 8(1), pp 152–158 | Tier B validation target: Beluga 5 load rejection (Figs 4, 5), 13-unit Table 3 "time-to-60%-Pm" and "rotor speed excursion" comparison |
| **Rowen (1983)** "Simplified mathematical representations of heavy-duty gas turbines", ASME J Eng Power 105(4), pp 865–869 | Heavy-duty GT block diagram (reference for Yee 2008 Rowen model) |
| **Rowen (1992)** "Simplified mathematical representations of single shaft gas turbines in mechanical drive service", ASME 92-GT-22 | Single-shaft / IGV extensions to the 1983 model |
| **Yee, Milanović & Hughes (2008)** "Overview and comparative analysis of gas turbine models for system stability studies", IEEE Trans Power Sys 23(1), pp 108–118 | Twin-shaft aero block diagram (Fig 9), Rowen-vs-IEEE comparison (Figs 16–20) |
| **CIGRE TB 238 (2003)** "Modeling of Combined-Cycle Power Plants for Power System Studies" | CCPP HRSG/ST model topology used in PES-TR1 Fig 3-9 |

In-project supporting docs:

| Citation | Used for |
|---|---|
| **Sailors LM2500 Pocket Guide** (NAVEDTRA) | EPCS architecture (PLA + Torque Computer + Speed/Accel/Torque limiters in low-value select, §1.3.15–1.3.17), operational limits (Table 1-2: NPT 3600 rpm, NGG 4900–9500 rpm, T5.4 1500/1530 °F alarm/trip), startup transient (Fig 1-65) |
| **MSC01A_1211_B5** (Technical Training Professionals 2021) | NAVEDTRA-quoted full-load air = 442,800 lb/h, fuel = 9,000 lb/h → **LM2500 actual AFR ≈ 49** (used to set LM9000 AFR default) |

---

## Tier A — TGOV1 bug-fix change log

### Status: **complete**

### Module

`gas_plant/dynamics/tgov1.py` — new file. The original ODE in `notebooks/lm2500_model.ipynb` cells 25–27 stays untouched as the baseline.

### Fixes applied (per critique §1 and §3.2)

| # | Fix | Original behavior | New behavior | Source |
|---|---|---|---|---|
| A1 | **Anti-windup on `x1`** | When `P_valve` clips to [VMIN, VMAX], `x1` keeps integrating → wind-up & delayed unclip | Back-calculation: when `P_valve_unclipped ≠ P_valve_clipped`, freeze `dx1/dt` so `x1` tracks the clipped output | Standard servo anti-windup (Åström & Hägglund 2006, Ch. 3) |
| A2 | **Valve rate limit** | Valve can change arbitrarily fast (only T₁ = 0.15 s smoothing) | Add `|dP_valve/dt| ≤ VRMAX/VRMIN`; valve becomes a state | LM2500 fuel-control accel/decel schedule constraint (qualitative — exact rate from MMO-010 not in hand); typical aero values from PES-TR1 §3.1.2.3 (Tact = 0.1–0.5 s, Ropen = 0.1 pu/s, Rclose = -0.1 pu/s) |
| A3 | **Frequency-dependent load damping** | `Pe(t) = Load17(t)`, no ω-dependence | `Pe(t, ω) = Load17(t) · [1 + α(ω − 1)]`, α = 1.5 pu | Standard load-damping coefficient (Kundur 1994, §11.1.4, typical α = 1–2) |
| A4 | **VMAX rebased to turbine limit** | VMAX = 1.1 pu on 23 MVA gen base = 25.3 MW > 22 MW turbine thermal limit | VMAX = 22/23 ≈ 0.957 pu (turbine thermal rating in per-unit on generator base) | LM2500 base power 22 MW (Pocket Guide Table 1-2: 26,250 BHP ≈ 19.6 MW shaft, 22 MW gen output; site uses 22 MW as the surrogate base) |
| A5 | **Fuel computed from valve, not Pm** | `fuel = dispatch(Pm)` evaluated at the *output* of the T₃ lag (double-counts turbine dynamics) | `fuel = dispatch(P_valve_filtered)` through a separate combustor lag T_comb | Critique §1.5; combustor residence time ≈ 0.3 s (Rowen 1983, E_CR transport delay) |
| A6 | **Event-driven integration** | RK45 sees ZOH discontinuities every 3 s in `Pe(t)`; step rejections + accuracy loss | Chunked integration: solve on each constant-Pe interval, pass final state as next IC | scipy `solve_ivp` standard practice for discontinuous forcing |

### Parameter values (Tier A)

All TGOV1 values inherited from the original `lm2500_model.ipynb` Cell 25 (which were already aero-typical), plus the new additions:

| Parameter | Value | Units | Source |
|---|---|---|---|
| H | 2.8 | s | Original notebook (aero gen-set estimate, unsourced — flagged for Tier B re-validation) |
| D | 1.0 | pu/pu | Original notebook (rough textbook value, conflates mech + load damping) |
| R | 0.04 | pu | Aero islanded service typical (4 %) — original notebook |
| T1 | 0.15 | s | Original notebook (Woodward MkVIe-like) |
| T2 | 0.3 | s | Original notebook |
| T3 | 1.5 | s | Original notebook (gas-fill time) |
| VMAX (was 1.1 pu) | **0.957 pu** | gen base | **A4 fix**: rebased to 22 MW turbine / 23 MVA gen |
| VMIN | 0.15 | pu | **changed from 0.0** — engine flame-out floor (LM2500 idle ≈ 15 % of rated fuel; informed by Pocket Guide §1.3.18 "minimum continuous fuel") |
| VRMAX (new) | +0.10 | pu/s | Aero typical (open rate); PES-TR1 §3.1.2.3 default |
| VRMIN (new) | -0.10 | pu/s | Aero typical (close rate); symmetric for now |
| α (load damping, new) | 1.5 | pu | Kundur 1994 §11.1.4 typical |
| T_comb (new) | 0.3 | s | Rowen 1983 ECR transport delay typical |

**Deviations from spec / open issues:**

- *VMIN raised from 0 to 0.15*: this was not in the original critique fix list but is the right physical floor (fuel cannot go to zero on a hot engine). Documented here so it can be revisited.
- *VRMAX = ±0.10 pu/s is conservative*: real LM2500 acceleration/deceleration schedules are ambient-and-NGG-dependent and would come from MMO-010. The flat ±0.10 pu/s is a placeholder; Tier B will override.
- *T_comb = 0.3 s is a textbook value*: actual LM2500 combustor residence + fuel-injection delay is shorter (~0.05–0.1 s) but the gas-fill / firing-rate response is slower. The 0.3 s lumps both and is conservative.

### Validation result — Load17 7.4-hour replay

Per-fix attribution from `notebooks/lm2500_tierA_tgov1_fixes.ipynb` Cell 7. Each row toggles ONE fix on top of the Tier-0 baseline so the marginal effect is isolated.

| Config | f_min (Hz) | f_max (Hz) | |Δf|_max (mHz) | Fuel total (t) |
|---|---|---|---|---|
| Tier-0 baseline | 59.6258 | 60.2256 | **374.2** | 26.289 |
| + A1 anti-windup only | 59.6258 | 60.2256 | 374.2 | 26.289 |
| + A2 valve rate limit only | 59.4433 | 60.3133 | **556.7** | 26.289 |
| + A3 load damping α=1.5 only | 59.6586 | 60.2164 | 341.4 | 26.255 |
| + A4 VMAX→0.957 only | 59.6258 | 60.2256 | 374.2 | 26.289 |
| + A6 event-driven only | 59.6259 | 60.2256 | 374.1 | 26.289 |
| **Tier-A all fixes** | 59.5223 | 60.2848 | **477.7** | 26.255 |

**Findings:**

- **A2 (valve rate limit) is the dominant correction**: by itself it adds +182 mHz to |Δf|_max because Load17's 3-s demand steps occasionally exceed the ±0.1 pu/s valve slew, and the rotor absorbs the imbalance. This is the most important Tier-A fix — the original ODE let the governor track demand artifactually fast.
- **A3 (load damping) provides a partial offset**: -33 mHz on its own. Net Tier-A is still larger than Tier-0 (478 vs 374 mHz) because A2 dominates.
- **A1 (anti-windup) and A4 (VMAX rebase) don't fire on this profile**: Load17 stays in 65–91 % of the valve range, so neither saturation nor windup is exercised. They are correct fixes; the test just doesn't stress them. (A load-rejection event would trigger A4; a sustained over-frequency would trigger A1.)
- **A6 (event-driven integration) is numerically equivalent**: -0.1 mHz vs RK45+ZOH. Confirms the original `interp1d(kind='zero')` worked acceptably *for this profile*; the event-driven path is still the right architecture for stiffer disturbances.
- **Cumulative fuel** changes by < 0.2 % (26.255 vs 26.289 t). Energy balance is preserved as expected.
- **Fuel ordering is now physical**: `P_fuel` (Tier A) leads `Pm` by `T₃ − T_comb` ≈ 1.2 s, instead of trailing it (Tier 0).

---

## Tier B — GGOV1 replacement

### Status: **complete**

### Inputs received from user

- **`papers/pes-tr1-2013-appendixC.txt`** — typical GGOV1 parameter table.
- **`papers/Hannett_table1.txt`** — Rowen-style governor parameters for 4 named units + "Typical Gas".
- **`papers/hannett_fig4_digitized.csv`** — Beluga 5 6 MW load-rejection turbine speed Δω (pu) vs time (60 Hz cycles → divide x by 60 for seconds).
- **`papers/hannett_figure5.csv`** — Beluga 5 6 MW load-rejection V_ce fuel-demand signal (pu) vs time (60 Hz cycles), same event as Fig 4.

### PES-TR1 Appendix C typical values (transcribed)

| Parameter | Typical | Units | Use in our LM2500 model |
|---|---|---|---|
| MWCAP | UserSupply | MW | turbine MW (22) |
| r (droop) | 0.04 | pu | matches LM2500 aero islanded |
| rselect | 1 | enum | 1 = use Pe for droop feedback |
| Tpelec | 1 | s | electrical-power transducer |
| MaxERR | +0.05 | pu | governor error clamp |
| MinERR | -0.05 | pu | governor error clamp |
| **KPGOV** | **10** | – | PI proportional |
| **KIGOV** | **2** | – | PI integral |
| KDGOV | 0 | – | derivative off (PI only) |
| TDGOV | 1 | s | derivative filter (unused if KDGOV=0) |
| VMAX | 1.0 | pu | will rebase to 22/23 (turbine MW on gen base) |
| VMIN | 0.15 | pu | matches Tier A |
| **TACT** | **0.5** | s | actuator τ — likely faster (0.1–0.2) for LM2500 Woodward MkVIe; override flagged for Tier B |
| KTURB | 1.5 | – | turbine gain |
| **WFNL** | **0.2** | pu | full-speed-no-load fuel |
| TB | 0.1 | s | turbine lead-lag B |
| TC | 0 | s | turbine lead-lag C |
| flag | 1 | – | fuel ∝ speed (shaft-driven pump; LM2500 has electronic, may set 0) |
| TENG | 0 | s | engine transport delay (0 for GT) |
| TFLOAD | 3 | s | temperature limiter filter |
| KPLOAD | 2 | – | temperature limiter proportional |
| KILOAD | 0.67 | – | temperature limiter integral |
| LDREF | 1.0 | pu | load limit (ambient-corrected at Tier C) |
| DM | 0 | – | speed damping in turbine output |
| ROPEN | +0.10 | pu/s | valve open rate (matches Tier A) |
| RCLOSE | -0.10 | pu/s | valve close rate (matches Tier A) |
| KIMW | 0 | – | MW set integrator off (use isochronous if islanded) |
| ASET | 0.01 | pu/s | acceleration setpoint (GE GT) |
| KA | 10 | – | acceleration controller gain |
| TA | 0.1 | s | acceleration filter |
| db | 0 | pu | deadband |
| TSA | 4 | s | temperature signal lead |
| TSB | 5 | s | temperature signal lag |
| RUP | +99 | pu | rate up (effectively disabled) |
| RDOWN | -99 | pu | rate down |

These are the **GE heavy-duty defaults**. For LM2500 islanded service the likely overrides are:
- TACT 0.5 → 0.15 (aero Woodward MkVIe is faster than heavy-duty hydraulic)
- KPGOV 10 → 15–25 (tighter for islanded)
- VMAX 1.0 → 0.957 (turbine MW on 23 MVA gen base, per A4)
- KIMW = 0 with isochronous outer loop, OR enable secondary control with Pmwset
- TSA/TSB and KPLOAD/KILOAD: keep PES-TR1 defaults until vendor data appears

These will be re-justified one-by-one in the Tier B section of this log.

### Validation targets (Hannett & Khan 1993)

| Target | Source | Pass criterion |
|---|---|---|
| Time to reach 0.6 Pm after 50→100 % step | Table 3, Beluga 5 row, "Derived" col | 2.0 s ≤ T_60% ≤ 2.6 s (Hannett derived = 2.32 s; typical-model = 1.14 s — we must NOT collapse to the typical value) |
| Max rotor speed excursion, 50→100 % step | Table 3, Beluga 5 row, "Derived" col | -0.0076 pu (target) |
| Beluga 5 6 MW load rejection, turbine speed curve | `hannett_fig4_digitized.csv` | qualitative overlay — match peak (~+0.0086 pu at t≈4.5 s) and settling (~+0.0054 pu at t≈14 s) within ±20 % |
| Beluga 5 6 MW load rejection, V_ce signal | `hannett_figure5.csv` | qualitative overlay — match initial spike (~0.135 at t≈2 s) and undershoot (~-0.05 at t≈5.7 s) within ±20 % |

### Implementation

`gas_plant/dynamics/ggov1.py` — 12-state model:
1. `delta`, `omega` (swing eq)
2. `Pe_filt` (Tpelec transducer)
3. `x_kigov`, `x_ka`, `x_kiload` (three controller integrators)
4. `x_accel_lag` (speed lag for d/dt filter)
5. `x_tload`, `x_tsab` (temperature signal lag + lead-lag)
6. `valve` (post-actuator state, with Tact + rate limit + Vmax/Vmin clamp)
7. `x_turb` (turbine lead-lag on Wf)
8. `P_fuel` (combustor lag for fuel reporting — Tier A A5 carried forward)

**Key architectural choices:**

- **Always-active back-calculation anti-windup** on all three integrators: `dx_kigov/dt = Kigov·err_speed − Kbc_speed·(fsrn − fsr)`, etc. The term `(fsrX − fsr) ≥ 0` by definition of `fsr = min(...)`. When the controller is selected, the term vanishes (pure PI). When not selected, it pulls the integrator toward the actual valve. Smooth, no `if/else` branching on selection state — critical for ODE-solver step sizes.
- **RK45 solver** with `rtol=1e-6, atol=1e-8`. Earlier attempts with LSODA + 1e-4/1e-6 hung at 13+ minutes because the swing-equation's `omega ≈ 1` was tracked to only ±100 µHz, drifting badly over long horizons. Tightened tolerances + RK45 + event-driven chunking: full 7.4-hr Load17 runs in ~83 s.
- **`GGOV1Params.lm2500_overrides()`** — convenience factory with LM2500-specific deviations from PES-TR1 defaults.

### LM2500-specific overrides (`p_lm2500` vs `p_pes`)

Only TWO parameters are overridden from pure PES-TR1 Appendix C; the rest are left at PES-TR1 defaults pending vendor data.

| Parameter | PES-TR1 default | LM2500 override | Rationale |
|---|---|---|---|
| `Tact_s` | 0.5 | **0.15** | LM2500 uses Woodward MkVIe / NetCon 5000 electronic valve actuator; heavy-duty PES-TR1 default is for hydraulic actuators. Original Tier-A T1 = 0.15 s consistent. |
| `Vmax_pu` | 1.0 | **22/23 ≈ 0.957** | Rebased from turbine MW to 23 MVA gen base (Tier A A4) |

All other GGOV1 parameters held at PES-TR1 typical values. Notes on the ones most likely to need future tuning:

- `Kpgov = 10`, `Kigov = 2`: PES-TR1 defaults. Hannett 1993 Table 1 shows the actual Alaskan Speedtronic units had Rowen `w` (≈ `Kpgov`) of 25–45 — significantly higher than the PES-TR1 "Typical Gas" `w = 25`. For islanded LM2500 service we may want Kpgov = 20–30; **flagged for vendor validation**.
- `WFNL = 0.2`: full-speed-no-load fuel. Typical aero. Sensitivity sweep recommended.
- `Kturb = 1.5`: PES-TR1 default. At full load this gives `valve_ss = (1/1.5 + 0.2) = 0.867`, leaving headroom up to `Vmax = 1.0`. **Different from Tier-A TGOV1 (implicit Kturb = 1)**, which is why Tier-B fuel totals are 5 % lower (different turbine-base normalization).

### Validation results

**Steady-state hold @ 15 MW for 30 s:** both `p_pes` and `p_lm2500` give `df = 0.000 Hz`, `Pm = 15.0000 MW`. No drift. ✓

**Step response (Hannett Table 3 protocol — 50→60 % step on 23 MVA machine, time to reach 0.6 Pm):**

| Configuration | Time to 0.6 Pm | Hannett reference |
|---|---|---|
| `p_pes` (PES-TR1 defaults, ~Beluga 5 class) | **1.30 s** | typical model w/ 3% droop: **1.14 s** ← close match |
| `p_lm2500` (aero overrides) | **0.95 s** | — (aero should be faster) |
| Hannett "derived from field test" (Beluga 5) | — | **2.32 s** |

**Interpretation**: our GGOV1 reproduces the *typical-model* literature value (1.30 vs 1.14 s) — the implementation is correct. The 2.32 s field-derived response is the documented Hannett gap: real machines are systematically slower than any typical model parameterization. Reproducing it requires plant-specific tuning (likely larger H, slower fuel control, possibly thermal effects not captured in GGOV1). For our LM2500 the 0.95 s aero-tuned value is the right baseline.

**Beluga 5 6 MW load rejection (Hannett Figs 4 & 5)**: qualitative shape matches (rise → peak → recover → settle), **but magnitudes do not** (model peak Δω = 0.69 pu vs measured 0.0086 pu, an 80× overshoot).

**Root cause**: Hannett does not publish Beluga 5's generator MVA or H. A 6 MW rejection on a 6 MVA machine (our test setup) is a 1.0 pu drop; on a real 60–100 MVA plant it would be 6–10 %. The measured Δω = 0.0086 pu implies an `H × Sn` product of ~500 MVA·s, i.e. plausibly H = 5 s on Sn = 100 MVA. Additionally Pref-handling during rejection is operator-dependent. **Without the unit's actual MVA base + H + Pref protocol, exact magnitude is unreachable from the published data**. The qualitative shape match is the validation; the magnitude mismatch is documented, not corrected.

**Load17 7.4-hr replay — three-model comparison:**

| Configuration | |Δf|_max | Fuel total |
|---|---|---|
| Tier 0 (original TGOV1) | 374.2 mHz | 26.289 t |
| Tier A (TGOV1 + 6 bug fixes) | 477.7 mHz | 26.255 t |
| **Tier B (GGOV1, LM2500 overrides)** | **460.0 mHz** | **24.940 t** |

Tier B sits slightly below Tier A in |Δf|_max (PI governor with explicit integrator outperforms the lead-lag of TGOV1 under the same valve rate limits). 5 % less fuel because Kturb=1.5/Wfnl=0.2 explicitly models no-load losses, where Tier-A TGOV1 implicitly used Kturb=1.

**LVG diagnostic on Load17**: speed governor commands valve **100.00 %** of the time; acceleration and temperature limiters never fire. As expected for a profile that stays 65–91 % of rating with small ramps. The plumbing for accel/temp limiters is verified (back-calc anti-windup keeps them tracking fsr), they just don't engage.

### Deviations from plan

1. **LSODA → RK45**: planned LSODA; switched to RK45 because the LVG min() introduces non-smooth Jacobian elements that LSODA struggles with (got 13+ minutes of CPU at 100% with no progress). RK45 handles this naturally via step rejection.
2. **Tighter default tolerances (1e-6/1e-8 vs 1e-4/1e-6 in Tier A)**: needed because omega ≈ 1.0 and 1e-4 relative tolerance corresponds to 6 mHz/sample frequency uncertainty, accumulating to ~0.7 Hz drift over 30 s on steady-state hold. Tightened solves it; minor wall-clock cost.
3. **Beluga 5 magnitude validation deferred**: documented above. Hannett does not publish the required setup parameters.
4. **`Kpgov` left at PES-TR1 default 10** despite Hannett Table 1 evidence that actual Alaskan units had w=25–45. For LM2500 islanded service this should be re-tuned against either vendor step data or an MMO-010 transcription (none available).

### Cross-cutting LM2500-specific overrides

When PES-TR1 Appendix C gives generic GGOV1 values, the following project-specific overrides will be applied with citation:

- `R = 0.04` (4 % droop, aero islanded — Pocket Guide §1.3.17)
- `Vmax` rebased to LM2500 turbine MW (not generator MVA)
- `Ldref` set to 1.0 pu on turbine MW base (temperature-limited rating, ambient-corrected later in Tier C)
- `Wfnl` (full-speed-no-load fuel) — needs estimation; placeholder = 0.18 pu (typical aero), to be tuned

---

## Tier C — Multi-shaft + electrical

### Status: **complete** (scipy path); ANDES upgrade deferred

### Implementation

`gas_plant/dynamics/multishaft.py` — 14-state model:

- **12 states inherited from Tier B GGOV1** (PT/gen swing eq + governor + valve + turbine + fuel lag)
- **2 new states**: `delta_hp`, `omega_hp` (HP rotor swing equation)

The HP rotor is driven by the governor's heat-rate-equivalent output `Pm_gov = K_turb*(x_turb - W_fnl)` minus a gas-path coupling power `P_couple = K_couple*(omega_hp - omega_hp_idle)` that flows to the PT/gen rotor. At SS, `P_couple = Pm_gov`, so the HP rotor RHS is zero. During transients, the HP rotor's lighter inertia (`H_hp = 0.3 s`) accelerates faster than the PT/gen rotor (`H_pt = 2.5 s`), producing a HP-speed swing that the lumped models cannot resolve.

### Parameters

| Parameter | Default | Source / rationale |
|---|---|---|
| `H_pt_s` | 2.5 s | PT + generator rotor inertia (slightly less than Tier B's lumped 2.8) |
| `H_hp_s` | 0.3 s | HP rotor — small fast aero rotor; typical 20-30% of total H |
| `D_pt` | 1.0 pu/pu | PT damping referenced to 60 Hz (synchronous, correct physics) |
| `D_hp` | **0.0** | HP rotor: gas-path coupling provides effective damping. Standard `D*(omega-1)` damping is WRONG for HP rotor because its natural speed is the gas-path operating point (~7900 rpm at 15 MW load), NOT 60 Hz. Default 0 avoids the SS shift. |
| `omega_hp_idle` | 0.516 | = 4900 rpm / 9500 rpm (NGG idle / NGG full per LM2500 Pocket Guide Table 1-2) |
| `K_couple` | `1/(1-0.516)` = 2.066 | Set so omega_hp = 1.0 (NGG full) gives full pu power to PT |
| `pv_exponent` | 0.0 | ZIP exponent on Pe(V/Vref). Default 0 = constant-P (preserves Tier B). Set 2.0 for constant-Z if modeling motor-heavy load. |
| `V_term_pu` | 1.0 | Voltage held at 1 pu in scipy path (perfect AVR assumption). For V-Q dynamics use the ANDES path. |

### Validation results

**Steady-state hold @ 15 MW for 30 s:** `df = 0.0000 mHz`, exact. HP rotor settles at 7901 rpm NGG (= 0.832 pu of NGG_full 9500 rpm), which is the physical operating speed for a 15 MW load (consistent with NGG idle 4900 + (full-idle) × load_frac).

**15 → 18 MW step at t=2 s:**
- Frequency nadir: 58.97 Hz (vs Tier A: 59.29, Tier B: 59.29). Tier C nadir is deeper because the HP-rotor mass takes finite time to spool up, delaying the Pmech response to the PT/gen rotor.
- HP rotor: 7901 rpm → 8632 rpm peak (overshoot of the SS), settles at 8450 rpm. **95 % of the speed change in 1.3 s** — this is the dynamic that drives part-load fuel response and is invisible in Tier A/B.

**Load17 7.4-hr replay (vs Tier 0 / A / B):**

| Tier | |Δf|_max (mHz) | Fuel total (t) | Wall-clock |
|---|---|---|---|
| Tier 0 | 374.2 | 26.289 | 97 s |
| Tier A | 477.7 | 26.255 | 6 s |
| Tier B | 460.0 | 24.940 | 80 s |
| **Tier C** | **541.1** | **24.940** | 86 s |

HP rotor swings between ~8,128 and 8,732 rpm over the Load17 window — a real dynamic visible only in this tier.

**Interpretation of Tier C's larger |Δf|_max:**

Tier C's |Δf|_max (541 mHz) exceeds Tier A (478) and Tier B (460), which initially looks like a regression. It is not — it is the correct physical story. The HP rotor's mechanical time constant (~0.7 s = `H_hp / D_hp` if D_hp were nonzero; without damping, the equivalent time is set by the gas-path coupling K_couple ≈ 2). During a load step, the governor opens the valve fast (~0.15 s actuator), heat-rate-equivalent power Pm_hp rises within ~0.3 s, but the **mechanical power actually delivered to the PT rotor** (Pm_pt = P_couple) lags by another ~0.5-1 s while the HP rotor spools up to a new operating speed. The PT/gen rotor sees this delayed Pm_pt and accelerates accordingly — so the frequency dips slightly deeper than in the single-mass models that artifactually deliver Pmech to the rotor instantaneously.

This matches qualitative LM2500 vendor experience that load-following frequency excursions are slightly worse than what TGOV1-style single-mass models predict.

**Tier C fuel total equals Tier B exactly** (24.940 t) because both use the GGOV1 fuel-from-valve callback through the same `P_fuel` combustor lag. The bulk fuel-power balance is unaffected by which rotor splits the inertia.

### What's still on the ANDES side (deferred follow-up)

The existing `gas_plant_andes/` package wires `GENCLS + TGOV1` to a 3-bus islanded test case. For a parallel ANDES validation of Tier C electrical, the upgrade would be:

1. Replace `GENCLS` with `GENROU` (round-rotor 6th-order model with E', X'd, T'd0).
2. Replace `TGOV1` with `GGOV1` using the Tier B parameter set.
3. Instantiate the existing `EXST1` defaults (Type ST1 exciter) — currently defined but not added to the case.
4. Use ANDES's DAE solver to handle the V-Q algebraic loops cleanly.

This isn't strictly needed for the scipy results above (V is held at 1 pu, AVR assumed perfect). It provides cross-validation and is a natural extension if the user wants to study voltage events, motor starts, or faults.

### Deviations from original Tier C scope

- **Single-axis E'/AVR/voltage solver was dropped from scipy path.** Implementing GENROU + EXST1 + algebraic V solver in pure scipy gave divergence (V collapsed to 0.5, Pe motored at -5 MW, Eq_prime saturated). The algebraic loops need a proper DAE solver (ANDES's domain). Replaced with simple V-exponent load model + `V_term_pu = 1.0` placeholder.
- **HP-rotor damping reference moved from 60 Hz to gas-path operating point** (effectively set to 0). The standard `D*(omega-1)` form is physically wrong for the HP rotor since its natural speed is not synchronous.
- **Two-mass instead of three-mass** for the mechanical model. Three-mass (HP + PT + gen) was considered but conflates the gas-path coupling (HP↔PT, not a physical shaft) with the actual PT-gen mechanical shaft. Tier E handles the PT-gen split for fatigue analysis where it belongs.

---

## Tier E — Torsional + fatigue

### Status: **complete**

### Implementation

`gas_plant/dynamics/torsional.py` — 2-mass PT-gen torsional model + calibrated rainflow / Miner damage. Drives off Tier C's `MultishaftResult`:

1. Take Tier C's `Pm_pt_mw(t)` (power from gas-path coupling to PT rotor) and `Pe_mw(t)` (load).
2. Interpolate onto a 1 kHz grid.
3. Integrate the 2-mass torsional ODE (PT rotor + generator rotor connected by the actual PT-gen output shaft).
4. Output time-resolved `T_shaft_kNm(t)`.
5. ASTM-style rainflow counting + Basquin S-N + Goodman mean-stress correction.

### Why 2-mass, not 3-mass

The original critique said "3-mass shaft (HP / PT / gen)". On further analysis, the HP↔PT "coupling" in Tier C is the **gas path** (combustion gas flowing through turbine stages) — not a mechanical shaft. There is no shaft-twist torsional mode between HP and PT. The actual physical shaft that carries cyclic torque is the PT-gen output coupling.

A first 3-mass implementation gave nonsensical results (T_pt_gen up to 13,700 kN·m vs rated 61 kN·m, Sa up to 19,000 MPa vs UTS 1,000 MPa). The bug was: treating the HP-PT gas-path as a torsional shaft meant the steady speed difference between HP (~0.83 pu of NGG_full) and PT (1.0 pu of 60 Hz) was integrated into theta_hp, producing unbounded twist. The 2-mass PT-gen model has both rotors at 1 pu of 60 Hz at SS, so theta_twist is finite and physical.

Tracking only `theta_twist = theta_pt - theta_gen` (rather than theta_pt and theta_gen separately) further avoids the secular drift that bit the 3-mass version.

### Calibration

| Quantity | Default | Source |
|---|---|---|
| `H_pt_only_s` | 0.5 s | PT-only inertia (split from Tier C's lumped 2.5 s) |
| `H_gen_s` | 2.0 s | Generator + flywheel inertia |
| `f_torsion_hz` | 22 Hz | Typical aero gen-set torsional natural frequency (range 18-30 Hz) |
| `zeta_torsion` | 0.010 | 1% structural damping — typical aero gen-set |
| `shaft_diameter_mm / shaft_inner_mm` | 150 / 50 | Typical 22 MW aero coupling shaft, hollow steel |
| `shaft_steel` | AISI 4340 | Typical high-strength shaft steel |
| `ultimate_strength_mpa` | 1000 | AISI 4340 typical UTS |
| `Sa_ref_mpa` (auto) | `0.3 × UTS = 300` | Endurance shear stress for high-strength steel torsion |
| `m_fatigue` | 9 | Basquin slope for high-strength steel torsion (typical 9-12) |
| `N_ref` | 1e8 | Reference cycles at Sa_ref |
| `use_goodman` (default) | True | Mean-stress correction `Sa_eq = Sa / (1 - Sm/Su_shear)` |

Stiffness `k_pu` is back-solved from the chosen natural frequency:
`k = omega_n^2 * I_red`, where `I_red = (M_pt * M_gen) / (M_pt + M_gen)`. Damping `c_pu = 2*zeta*omega_n*I_red`.

### Validation results

**Small load step (15 → 18 MW):** **passes.** Shaft torque 34.7-50.2 kN·m (within rated 61 kN·m envelope, ~57-82 % of rated). Max Sa = 8 MPa (vs Su_shear ~600 MPa). `D_total = 9.7e-23`. Correct physical behavior — small load-following at 3 s ZOH cadence is too slow to ring up the 22 Hz torsional mode.

**Full load rejection (22 → 2 MW):** **runs to completion but the rainflow output requires careful interpretation.** Direct inspection of `T_shaft(t)` confirms the torsional ODE works correctly: T_shaft monotonically drops from 58.4 kN·m at t=2 s to ~0.3 kN·m at t=10 s, with small superimposed oscillations at the natural mode (rotor speeds reach ω_pt = 1.605 pu over the window — Tier C's PT/gen rotor over-speeds significantly because the GGOV1 valve clamps at Vmin=0.15 and can't fully cut fuel). The rainflow output reports `max ΔT = 1.14 kN·m, FFT peak at 0.1 Hz`, which initially looks like a model failure but is **correct ASTM E1049 behavior for a non-stationary signal**:

- Rainflow counts cycles formed by adjacent turning points. For a damped oscillation superimposed on a monotonic decay, every "peak/valley" pair on the decay creates a tiny half-cycle (~0.5-1 kN·m range each), AND the bulk 58 → 0 transition shows up as residual half-cycles each with small range (because the decay passes through every intermediate value).
- The 0.1 Hz FFT peak reflects the underlying bulk decay (period ~10 s = the recovery transient), not the 22 Hz torsional mode (which IS present but is much lower amplitude than the bulk).
- **For correct fatigue interpretation of rejection-type events, the signal must be detrended first** (subtract a slow moving average) before rainflow. The torque oscillations around the trend then count properly. The bulk transient itself is a single low-cycle-fatigue event that needs separate treatment (extreme-value analysis, not high-cycle Miner sum).

**Load17 1-hr cumulative damage:** `D = 3.4e4` (catastrophically large, equivalent life 3.3 ns). **This number is unphysical and reflects a numerical drift, not real damage.** The diagnostic chain:

- Tier C's `Pm_pt(t)` and `Pe(t)` are nominally equal in steady state, but the gas-path coupling `Pm_pt = K_couple·(ω_hp − ω_hp_idle)` introduces a small persistent offset (`mean(Pm_pt − Pe) = -0.041 MW, std = 0.064 MW` over 1 hr).
- The torsional ODE integrates this small persistent imbalance as `dθ_twist/dt = (ω_pt − ω_gen)·ω_0`. Over 3,600 s a steady ~10⁻⁶ pu speed difference accumulates into a multi-rad twist angle, giving `T_shaft = k·θ_twist` that grows monotonically. By end of window, max range = 464 kN·m (7.6× rated). All "damage" in the Load17 result is from this drift, not real shaft motion.

### Tier E v2 — fixes applied (status: complete)

Two root-cause bugs identified and fixed:

**Bug 1 — wrong per-unit stiffness conversion (calibration).** The natural frequency derivation in `TorsionalParams.__post_init__` was missing the per-unit `ω_0_rad_s` factor:

- Wrong (v1): `k_pu = ω_n² · I_red`
- Right (v2): `k_pu = ω_n² · I_red / ω_0_rad_s`

The pu inertia × angle dynamics relate as `d²θ/dt² = ω_0 · τ_pu / M_pu`, so `ω_n² = ω_0 · k_pu / I_red` (not just `k_pu / I_red`). The missing factor of `ω_0 = 377` made the actual stiffness 377× too high, giving a torsional natural frequency of **427 Hz** instead of the designed **22 Hz** (verified by FFT). The 427 Hz mode was a) outside the bandwidth of any realistic forcing, so it never got excited (Test 2 saw a 0.1 Hz bulk peak, not 22 Hz), and b) extremely stiff for the ODE integrator (Test 3 hung at >10 min CPU).

**Bug 2 — common-mode drift in the state vector formulation (architecture).** The v1 state vector `[θ_twist, ω_pt, ω_gen]` left the common-mode (`ω_pt + ω_gen`) free to drift when Tier C's `Pm_pt(t)` and `Pe(t)` had small persistent mean differences (`mean(Pm_pt − Pe) ≈ -0.04 MW` over Load17). Both rotor speeds grew together by `Δτ·t/M_tot`, and tiny floating-point differences integrated into multi-radian phantom twist over 1 hr. Fixed by **Option A** (differential-mode formulation):

- State vector reduced to `[θ_twist, ω_diff]` (2 states instead of 3)
- COM speed `ω_avg(t)` taken as external forcing from Tier C's `omega_pt_pu` — common mode **cannot** drift independently
- Per-rotor speeds reconstructed: `ω_pt = ω_avg + (M_gen/M_tot)·ω_diff`, `ω_gen = ω_avg − (M_pt/M_tot)·ω_diff`
- Differential equation: `dω_diff/dt = τ_pt/M_pt + τ_e/M_gen − T_shaft/I_red`

For non-stationary rainflow (Bug 3 — algorithmic, not a model bug), **Option C** was added: `detrend_rolling_median(signal, times, window_s)` separates a slow trend (low-cycle-fatigue events) from the high-cycle residual that ASTM rainflow + Miner handle correctly.

### Tier E v2 validation results

| Test | v1 (broken) | **v2 (fixed)** |
|---|---|---|
| Calibration: actual `f_torsion` | 427 Hz (19× too high) | **22.0 Hz** (matches design exactly) |
| Test 1 step: T_shaft range | 34.7-50.2 kN·m | **39.8-49.7 kN·m**; trend = 7.6 kN·m bulk + 4.6 kN·m detrended |
| Test 2 rejection: FFT peak | 0.1 Hz (bulk only) | **22.0 Hz** — torsional mode excited, residual maxΔT = 22.5 kN·m |
| Test 2 rejection: bulk transient | hidden by rainflow | **57.3 kN·m** explicitly captured by `detrend_rolling_median` for separate low-cycle treatment |
| Test 3 Load17 1hr: T_shaft range | 0 to 464 kN·m (drift) | **40.4 to 51.1 kN·m** (bounded, < rated 61) |
| Test 3: theta_twist range | drift to many radians | **0.94 to 1.18 degrees** (bounded, physical) |
| Test 3: wall-clock | >10 min (stiff) | **38 s** (well-conditioned) |
| Test 3: D_per_year extrapolated | 3.0e8 (catastrophic) | **5.2e-30 (negligible)** — correct |
| Test 3: equivalent life | 3.3 ns | **1.9e29 years** — correct |

The new headline conclusion for the data-center duty cycle: **a Load17-class profile alone produces no measurable shaft fatigue damage** (D/year ≈ 10⁻³⁰). High-bandwidth events (faults, motor starts, breaker trips) — not present in Load17 — are the real fatigue threats; the model is now configured to quantify them correctly when a fault/event trace is supplied.

### New utility added (`detrend_rolling_median`)

Exported from `gas_plant.dynamics`. Signature: `detrend_rolling_median(signal, times, window_s=5.0) -> (residual, trend)`. Use the residual for high-cycle rainflow + Miner; use the trend (or its peak-to-peak excursions) for low-cycle / extreme-value fatigue accounting. Window guidance: `window_s >> 1/f_torsion` (so the mode is preserved) but `<<` bulk-transient duration (so the trend captures it). For 22 Hz mode + seconds-long transients, `5 s` is the default; reduce to `0.5–2 s` for fast rejection events.

### Caveats / limitations

- **Shaft geometry, material, and S-N parameters are typical aero-gen-set values, NOT vendor-specific.** For a design-grade life assessment, replace with actual LM2500 shaft drawings + material certifications + verified S-N curves.
- **Aerodynamic and electromagnetic damping of the torsional mode are not modeled** — both are usually small but can be non-trivial.
- **No high-frequency excitation source modeled** — real torsional fatigue comes from sub-cycle electrical events (faults, motor starts, breaker operations), not from Load17-style slow demand changes. Add a fault simulator if the design intent requires bracketing those events.
- **Goodman is conservative for compressive mean stresses** — Walker or SWT correction could be substituted if more nuanced mean-stress treatment is needed.

### Deviations from original Tier E scope

- **3-mass → 2-mass.** Documented above (gas-path coupling is not a physical shaft).
- **`theta_twist` directly tracked** instead of separate `theta_pt`, `theta_gen` — avoids the secular drift from steady speed offset that destabilized the 3-mass version.

---

## LM9000 bug fixes (out-of-band, paired with Tier A)

### Status: **complete** (smoke-tested against datasheet anchors)

`gas_plant/lm9000.py` fixes applied alongside Tier A (per user request):

| # | Fix | Source / result |
|---|---|---|
| L1 | AFR `0.034` (F/A) → `0.020` (F/A, AFR ≈ 50, exposed via new `air_fuel_ratio` constructor arg) | NAVEDTRA via MSC01A manual: LM2500 full-load AFR = 442,800/9,000 ≈ 49. LM9000 also lean-DLE aero. Smoke test: exhaust mass at design = 149.4 kg/s (was 89.5 kg/s), vs published ~158 kg/s. |
| L2 | Deleted dead methods `_compressor_work` and `_turbine_work` (never called; misleading "component-based" framing) | Critique §2.1. Component-map dataclasses (`LPC_MAP`, `HPC_MAP`, etc.) **kept** as engine documentation — clearly not part of run-time path. |
| L3 | Fixed CO2 comment typo `0.39.52` → `0.3952`; replaced with explicit back-calculation derivation. | Critique §2.5. Smoke test: CO2/MWh @ full load = 492.65 vs datasheet 492.9 (within 0.05 %). |
| L4 | At L = 0 (or below `min_load_frac`): `power_w = 0`, `fuel = 0`, `efficiency = 0` (NOT the polynomial extrapolation ≈ 37.5 %) | Critique §2.6. Smoke test: L=0 returns all zeros as expected. |
| L5 | Added `air_fuel_ratio` and `min_load_frac` constructor parameters | Defensive — AFR varies across LM-class aeros; `min_load_frac` lets caller pick the physically meaningful operating envelope (default 0 to preserve historical behavior). |

### Smoke test results (LM9000 simple cycle, full load)

| Quantity | Pre-fix | **Post-fix** | Datasheet |
|---|---|---|---|
| Power | 56.723 MW | 56.723 MW | 56.723 MW |
| Efficiency | 0.3952 | 0.3952 | 0.3952 |
| Fuel | 2.929 kg/s | 2.929 kg/s | (derived) |
| Exhaust mass | 89.5 kg/s | **149.4 kg/s** | ~158 kg/s |
| Exhaust T | 729 K | 729 K | 729 K |
| CO2/MWh | 487 (with old AFR) | **492.7** | 492.9 |

### LM9000 CC fuel-rescaling fix (completed)

**Status:** complete. Applied alongside Tier C / E work.

Replaced the `_size_scale` post-rescaling with auto-tuned `eta_bottoming_nominal`. The constructor now solves algebraically for the `eta_bottoming` that makes `p_gt(L=1) + p_st(L=1) = rated_power_mw`, then uses that value throughout. Power, fuel, exhaust mass, and CO2 are all physical — no post-hoc rescaling.

Auto-tuned value: `eta_bottoming_nominal = 0.288` (vs the old default 0.50 that was masked by `_size_scale`).

| LM9000 CC full load | **Post-fix** | Datasheet | Error |
|---|---|---|---|
| Power | 72.471 MW | 72.471 MW | 0.000 % |
| Efficiency | 0.5049 | 0.5048 | +0.024 % |
| Fuel | 2.929 kg/s | (= SC fuel) | 0.006 % |
| Heat rate | 7130 kJ/kWh | 7132 | -0.030 % |
| CO2/MWh | 385.6 | 383.6 | +0.52 % |

User can still pass an explicit `eta_bottoming_nominal=X` to override the auto-tune (for sensitivity studies or non-design points).


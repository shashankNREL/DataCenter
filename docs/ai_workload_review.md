# Review: `notebooks/lm2500_ai_workload.ipynb` (MIT SuperCloud AI-workload case)

V&V review, 2026-07-18. Companion to `docs/vv_log.md`. All changes described
here were applied directly to the notebook and it was re-executed end-to-end
under the pixi environment against the post-V&V models.

## Verdict up front

- **Code logic:** the pipeline architecture (trace → Tier C → Tier E →
  rainflow/Miner) is sound, but the notebook was **not runnable as
  committed** (hardcoded path from another machine, stale pre-V&V API) and
  had three data-construction defects (aggregation jitter, multiplicative
  PUE, unstated correlation assumption) — all fixed.
- **Physics:** the models are exercised far outside their validity range in
  the baseline run. The frequency trajectory in the *original committed
  outputs* spans **44.8–67.3 Hz** with the gas-generator spool reaching
  **−0.48 pu (spinning backwards)** — the machine has tripped and the model
  has broken down, silently.
- **Conclusions:** the original headline ("years to shaft-fatigue failure ≈
  12,787") is **not correctly derived** — it is Miner's-rule accounting on a
  post-trip, non-physical trajectory. The correct top-line conclusion, now
  added to the notebook, is operational, not fatigue-related: **a single
  LM2500 trips on this load within the first minute.**
- **Battery question: yes — a BESS (or equivalent fast buffer) is
  required**, and the notebook now sizes it and verifies the buffered
  configuration rides through. See §5.

---

## 1. Defects found and fixed

### 1.1 Not runnable as committed

| # | Defect | Fix |
|---|---|---|
| N1 | `PROJECT_ROOT = Path('/Users/syellapa/...')` — another machine's home directory | Portable `Path.cwd()`-based root |
| N2 | `mps_params.ggov1.P_turbine_mw` — attribute renamed to `Trate_mw` in V&V Phase 0; notebook crashes against current library | Updated to `Trate_mw` |
| N3 | `boto3` / `pyarrow` not in the project environment | Added to `pixi.toml` |

### 1.2 Trace-construction physics

| # | Defect | Fix / disposition |
|---|---|---|
| T1 | **Slot-sum aggregation.** Summing raw samples per 100 ms slot lets a GPU drop out of (or double into) a slot purely from its reporting jitter (measured per-GPU cadence: median 0.106 s, p90 0.117 s), adding artificial 100 ms sawtooth. Measured impact: ~20 % inflation of p99 |dP/dt|. | Replaced with per-GPU zero-order-hold onto the 100 ms grid, then sum — the aggregate changes only when a GPU actually reports a new value. |
| T2 | **Multiplicative PUE.** Multiplying the whole trace by PUE = 1.25 makes the cooling plant follow GPU load at 100 ms, inflating every step by 25 %. Chillers/fans respond over minutes. | PUE overhead modeled as a **constant additive baseload** = (PUE−1)·mean(IT); only the IT trace is scaled. |
| T3 | **Unstated perfect-correlation assumption.** The densest 2 h window contains only **6 concurrently active GPUs** (not the ~35 in the whole 1 GB sample); hitting an 18 MW peak requires a ×~13,000 multiplier, i.e. ~13,000 six-GPU clusters fluctuating **in perfect lockstep**. A facility of independent jobs would be smoother by roughly √(N_facility/N_sample); facility-wide synchronized training (all-reduce barriers) approaches the lockstep case. | Documented in the notebook as a **worst-case (fully correlated) bound**; every downstream number is conditional on it. |
| — | The notebook's claim that nvidia_smi.csv is native 100 ms telemetry **was verified true** (median per-GPU Δt = 0.106 s) — despite the repo's own notes recommending dcgm.csv for transients. No change needed. |

The corrected trace (18 MW target peak): mean 10.7 MW, min 5.2 MW, 100 ms
steps up to **+9.6 / −10.5 MW** (p99 |step| = 6.3 MW ≈ 29 % of the 22 MW
rating in a tenth of a second).

### 1.3 Analysis gaps

| # | Gap | Fix |
|---|---|---|
| A1 | **No trip assessment.** The study quantified fatigue but never asked whether the unit stays online, despite its own frequency trace leaving any operable range. | New Section 6a: excursions vs the 59.4–60.6 Hz continuous band, 58.4 Hz (~30 s permitted, PRC-024-class / typical 81U), and 57.8 Hz (instantaneous-trip territory); first-trip time reported. |
| A2 | **No mitigation analysis.** | New Section 6b: BESS sizing by low-pass power split + verification re-run of Tier C on the buffered demand. |
| A3 | **Fatigue computed on an invalid trajectory.** | New Section 6c: Tier E fatigue re-computed on the buffered (physically valid) run; baseline fatigue numbers explicitly labeled non-physical. |
| A4 | Notebook predates the V&V Phase 0 fixes; its stored outputs came from models with double damping, mixed per-unit bases, and the biased fuel mapping. | Re-executed end-to-end against the post-V&V models. |

---

## 2. Why the original conclusion was wrong (and what the data already showed)

The original committed outputs contained the evidence:

```
freq:  min=44.790  max=67.250 Hz
omega_hp: min=-0.4824  max=0.8347 pu
Years to D=1 = 12786.58
```

- Below ~57.8 Hz a 60 Hz machine's underfrequency protection acts within
  cycles to seconds; 44.8 Hz is far past any ride-through curve. The unit is
  **off-line** early in the window; everything after is counterfactual.
- `omega_hp = −0.48 pu` is the gas-generator spool spinning backwards —
  impossible; it flags that the linear gas-path coupling and the swing
  equation are being driven far beyond their validity (|Δω| ≫ 5 %).
- Miner's-rule damage integrated over that trajectory ("12,787 years") is
  therefore not a statement about any operating machine.

The notebook's *methodological* fatigue machinery (HCF/LCF split via rolling
median, disjoint frequency bands, Goodman correction) is fine — it was the
missing operability check that invalidated the conclusion.

## 3. Results of the corrected run (post-V&V models)

All numbers from the re-executed notebook (2 h window, 100 ms trace,
per-GPU-ZOH construction, additive PUE; scale multiplier 11,345×, constant
overhead 2.14 MW).

### Corrected trace

| quantity | value |
|---|---|
| facility power | min 5.24 / mean 10.68 / max 18.00 MW |
| 100 ms steps | max +9.60 / −10.51 MW; p99 \|dP/dt\| = 63.2 MW/s |

### Baseline — single LM2500, no battery: **TRIPS at t = 9.2 s**

| quantity | value | reading |
|---|---|---|
| frequency range | 20.7 – 71.1 Hz | total loss of frequency control |
| first crossing < 57.8 Hz | **t = 9.2 s** | instantaneous-trip territory 0.2 min into the window |
| time below 57.8 Hz | 4,048 s (of 7,200) | — |
| time outside 59.4–60.6 Hz | 5,757 s; longest low-side dwell 182 s | — |
| ω_hp range | −3.38 … 0.90 pu | gas generator "spinning backwards": model far outside validity |
| fatigue tally | D = 4.2e-8 ("5,396 years") | **not physically meaningful** — accounting on a post-trip trajectory |

Everything after t ≈ 9 s in the baseline run is counterfactual: a real unit
is off-line and the swing/governor model is far outside its validity range
(|Δω| ≫ 5 %). The notebook now states this in place.

### With battery buffer (first-order split, GT follows LPF of demand)

BESS sizing from the trace (battery = demand − LPF(demand)):

| T_SPLIT (s) | battery power (MW) | battery energy (MWh, max swing) | GT max ramp (MW/s) |
|---|---|---|---|
| 5  | 9.88 | 0.010 | 1.98 |
| 10 | 9.92 | 0.018 | 0.99 |
| 30 | 8.73 | 0.043 | 0.29 |

Verification re-runs of Tier C on the buffered demand:

| configuration | frequency range | time outside 59.4–60.6 Hz |
|---|---|---|
| GT alone | 20.7 – 71.1 Hz | 5,757 s + trip |
| GT + BESS (τ = 10 s), droop (rselect=1) | **60.000 – 60.768 Hz** | 401 s (all high side) |
| GT + BESS (τ = 10 s), isochronous (rselect=0) | **59.80 – 60.32 Hz** | **0 s** |

The residual 401 s in the droop case is not a transient failure — frequency
never dips below 60.000 Hz. It is the steady-state **droop offset**: with
Pref frozen at the initial (near-peak) load, the unit sits up to
R·ΔP·60 ≈ 0.77 Hz above nominal whenever demand is well below Pref. The
standard remedies are secondary control (Pref tracking / KIMW) or
isochronous mode for a single islanded unit; the isochronous verification
(15-min segment, same buffered demand) holds the band with margin.

### Fatigue in the physically valid (buffered) configuration

Shaft torque 16.1–33.5 kN·m (rated ~61); D_window = 6.0e-10 →
per-year-equivalent D ≈ 2.6e-6, i.e. **~4×10⁵ years to D = 1** — torsional
fatigue is a non-issue when the fast content is buffered, consistent with
the Tier E screening finding that sub-Hz load-following cannot excite the
22 Hz mode. (All Tier E caveats apply: placeholder shaft geometry, material,
and S-N parameters — screening, not design-grade.)

## 4. Is the physics properly implemented now?

- The governor/turbine/torsional layers themselves carry the V&V evidence
  from `docs/vv_log.md` (ANDES cross-check to 0.124 mHz, Hannett benchmarks,
  36 invariant/regression tests).
- Within this notebook, the remaining physics caveats are inherited and
  documented: fully-correlated trace scaling (worst case), the placeholder
  Tier C gas-path coupling (qualitative transients), placeholder torsional
  parameters (screening only), and quasi-steady aerothermodynamics (no
  combustion heat-soak).
- The **baseline (no-BESS) segment after the first trip crossing should be
  read as illustrative only** — the notebook now says so in place.

## 5. Battery recommendation

**Yes — implement a battery (or flywheel-class) buffer for this use case.**
The quantitative basis, now computed in notebook Section 6:

- **Need:** 100 ms load steps up to ~10.5 MW against a machine whose valve
  slews 2.2 MW/s and whose rotor stores only 2H·Sn ≈ 129 MJ/pu — the inertia
  covers a 10 MW step for well under a second before frequency leaves the
  operable band. Without a buffer the unit trips in the first minute.
- **Sizing rule used:** first-order low-pass split with time constant
  `T_SPLIT`; GT follows the smooth component (ramps verified ≤ its 2.2 MW/s
  capability at `T_SPLIT = 10 s`), battery supplies the residual. Battery
  power = max |residual|; battery energy = max swing of its running
  integral. The buffered demand was re-simulated through Tier C to verify
  frequency stays within 59.4–60.6 Hz.
- **Numbers for this trace:** battery power ≈ **10 MW** (set by the largest
  fast swing — note it barely decreases with T_SPLIT, because the dominant
  events are large near-instant steps), battery energy ≈ **18 kWh** at
  T_SPLIT = 10 s (43 kWh at 30 s). Ratio ≈ 550 C-equivalent: this is a
  *power-type* buffer (high-C LFP, supercapacitor, or flywheel), not an
  energy-shifting battery. UPS assets many data centers already own can
  serve part of this role if allowed to grid-form. Pair the buffer with
  **isochronous governor mode** (rselect=0): with droop the unit rides
  through but sits up to 0.77 Hz high on droop offset; isochronous holds
  59.80–60.32 Hz with zero band violations.
- **Alternatives / complements** worth noting in any design study: N+1
  paralleled smaller GTs (shares the step across machines), workload-side
  power smoothing (ramp-limited job launch, staggered checkpoints — the
  knob hyperscalers actually use), and isochronous governor mode
  (`rselect=0`, now implemented) which removes steady-state offset but does
  not change the transient physics.
- **Caveat cutting the other way:** the trace is a fully-correlated worst
  case (T3). If the real facility's jobs are even partially decorrelated,
  the required battery power shrinks roughly with √N. The sizing here is
  conservative by construction.

## 7. Toward a realistic scaling of the MIT SuperCloud data (follow-up, 2026-07-18)

The current construction (one 6-GPU window × 11,345, lockstep) is a
*bounding* case. What "realistic" means depends on tenancy — and that is the
first decision to make:

- **Single-tenant training campus** (one giant job spans the facility):
  synchronized compute/communicate phases make the whole facility swing
  coherently — published LLM-pretraining experience shows facility-level
  power oscillating by tens of percent at ~0.5–2 Hz. For that design case
  the lockstep bound is *approximately right*, and the notebook's
  conclusion stands as a design requirement.
- **Multi-tenant cluster** (SuperCloud-like, many independent jobs): the
  aggregate is far smoother — relative fluctuation falls roughly as
  1/√(number of independent jobs).

Recommended improvements, in order of value per effort:

1. **Job-level bootstrap composition (main recommendation).** Build the
   facility trace as a sum of *independently sampled, randomly time-shifted
   per-job traces* from the dataset itself (group by `id_job`; stream more
   S3 byte ranges — the 1 GB sample holds 50 jobs — or use the per-job
   `dcgm.csv` files). Draw jobs until Σ`req_gpus` reaches the target GPU
   count. This preserves genuine *intra-job* synchronization (the real
   all-reduce sawtooth) while decorrelating *across* jobs — exactly how a
   multi-tenant facility behaves. Generate an ensemble of bootstrap traces
   and report distributions (of |dP/dt|, nadir, BESS size), not one trace.
2. **Correlation-parameterized family.** Blend the two constructions,
   `L_ρ = ρ·L_lockstep + (1−ρ)·L_bootstrap`, and sweep ρ ∈ [0, 1]. Present
   trip verdicts and BESS sizing *as a function of ρ*; the current notebook
   is the ρ = 1 endpoint. This turns an arbitrary assumption into an explicit
   design axis.
3. **Scheduler-driven synthesis.** Use `scheduler_data.csv` (arrivals,
   `req_gpus`, durations) to reproduce realistic occupancy and job churn.
   Job *launch/kill* events are the largest genuinely-correlated steps a
   multi-tenant facility sees (a whole job's power appears/vanishes at
   once) — the 2 h dense window misses them, along with diurnal structure
   and checkpoint stalls.
4. **Model the electrical chain between GPU and generator.** nvidia_smi
   reports DC power at the GPU; the generator sees it through VRM/PSU
   (≈95 % efficiency), rack and facility UPS capacitance, which low-pass the
   sub-100 ms content. Newer platforms (GB200 class) ship *built-in* power
   smoothing (ramp limits + energy buffer) precisely for this problem. A
   rack-level first-order filter (tens of ms) plus a vendor-smoothing
   option would make the trace the *meter-side* load, which is what matters.
5. **Hardware-era correction.** SuperCloud is V100-era (~250–300 W/GPU);
   H100/B200 are 700–1200 W with larger, faster swings. Re-envelope the
   per-job traces (scale amplitude, keep timing statistics) or splice in
   published H100-class job power logs.
6. **Dynamic PUE.** Replace the constant overhead with a first-order lag on
   IT power (τ ≈ minutes) plus ambient dependence — matters for the slow
   band and the temperature limiter, not the governor.
7. **Validation target.** Whatever synthesis is used, validate its power
   spectral density and step-size distribution against published
   facility-scale measurements of AI campuses before trusting downstream
   numbers.

## 8. Does a fleet of turbines help? (follow-up, 2026-07-18)

Quantified with the aggregated-equivalent-machine model (N identical units,
equal droop sharing on a common bus: `Sn = N·23`, `Trate = N·22`; per-unit H
unchanged). Screening on the worst 15-min segment (contains the −10.5 MW
step; full-trace confirmation for N = 3, 4 below):

| N units | segment f range (Hz) | segment < 57.8 Hz | full-2h verdict | per-unit avg load | fuel vs N=1 |
|---|---|---|---|---|---|
| 1 | 21.8 – 69.5 | 579 s | **TRIP** (t = 9.2 s) | 48 % | 1.00× |
| 2 | 40.5 – 66.6 | 235 s | **TRIP** | 24 % | 1.34× |
| 3 | 59.25 – 60.83 | 0 s | **FAILS** — collapses during a worse event cluster later in the window (5,943 s < 57.8 Hz) | 16 % | 1.68× |
| 4 | 59.58 – 60.55 | 0 s | **PASSES**: 59.53–60.68 Hz, no trip, 2.2 s marginally out of band | 12 % | 2.02× |
| **2 + BESS (τ=3 s, 8.4 MW / 5.5 kWh)** | 59.80 – 60.44 | 0 s | (segment; full-2h GT+BESS compliance already shown at N=1 in §3) | 24 % | 1.34× |

Note the N = 3 lesson: the worst 15-min segment (selected by largest single
step) is *not* the worst stretch of the window — N = 3 survived the
screening segment but collapsed on the full trace. Fleet sizing from short
excerpts is unsafe; use the full window (or an ensemble, §7).

**Yes, a fleet helps, through three mechanisms that all scale with N:** the
same MW step is a factor-N smaller in per-unit terms, aggregate stored
rotor energy (2H·Sn) is N× larger, and aggregate valve slew is N× 2.2 MW/s.
On this (worst-case) trace, **it takes N = 4 gas turbines to ride through
unassisted** (88 MW of installed capacity for an 18 MW peak load).

**But a fleet alone is the expensive way to buy ride-through.** The units
end up at 12–16 % load where the Willans line puts efficiency at 15–18 %,
i.e. a **1.7–2× fuel burn** for the same energy, likely below the DLE
combustor's emissions-compliant minimum load, with 3–4× the maintenance
exposure. The battery buys the same fast-band coverage for tens of kWh of
storage with no fuel penalty.

**Recommended architecture: N = 2 + power-type BESS + isochronous load
sharing.** Two units give N−1 redundancy (22 MW ≥ 18 MW peak — a single-unit
plant blacks out the facility on *any* GT trip, workload transients aside);
the ~8 MW battery covers the sub-10 s band (verified compliant above); and
per-unit loading stays at a workable 24 % average (41 % on one unit during
maintenance windows). Caveats: the equal-sharing aggregation assumes
identical units on a common bus (standard approximation — transient
unit-to-unit sharing imbalance is not modeled), multi-unit isochronous
operation requires load-sharing controls, and everything inherits the
fully-correlated trace bound (§7), which makes these sizings conservative.

## 9. Files changed

- `notebooks/lm2500_ai_workload.ipynb` — fixes N1–N2, T1–T2; caveat T3;
  new Sections 6/6a/6b/6c and a "Revised conclusions" cell; re-executed.
- `pixi.toml` — added boto3, pyarrow (N3).
- `data/nvidia_smi_first_1gb.parquet` — regenerated cache (gitignored;
  ~90 MB).
- This review: `docs/ai_workload_review.md`; pointer added to
  `docs/vv_log.md`.

# Doc B — Implementation Specification
## Asset Life Models under Variable Electrical Loading

**Version:** 0.1.0-draft · **Date:** 2026-10-19 · **Status:** specification for review
**Companion:** `docs/digital_twin_asset_life_models.tex` (Doc A — evidence & methods)

---

## 0. How to read this document

Doc A asks *can these claims be supported?* and answers honestly, with caveats.
**This document asks *what do I build, and how do I know it is right?* and commits.**

Every equation here carries five fields Doc A lacks:

| Field | Meaning |
|---|---|
| **Default** | A number you can type today. Not a suggestion to find one. |
| **Source** | Where the number came from, or `FIXTURE` if synthesized for testing. |
| **Range** | Validity bounds. Outside them the code raises, it does not extrapolate. |
| **Code** | The exact function signature. |
| **Status** | `impl:` and `evidence:` tiers (§0.3). |

Caveats are not deleted — they are **relocated into test assertions and the
`evidence` tier**. Doc A's 112 "Do not …" sentences become executable checks.
A caveat that cannot be expressed as a test or a tier belongs in Doc A, not here.

### 0.1 Scope of v0.1 — asset models first

**In scope.** Four asset life models, each independently runnable, each driven by
*analytic excitation signals* and validated against closed-form or published results.

**Explicitly deferred to v0.2+ (not in this document):**

- Workload → facility power modelling; GPU/job traces; ExaDigiT/RAPS coupling
- Site-wide power balance across assets; network power flow
- Scheduler / dispatch policy optimisation
- MPI, HELICS, FMI/FMU, Modelica
- Transformer oil oxidation chemistry (see §2.6 — not identifiable from realistic sampling)
- Absolute remaining-life forecasts for any asset

**Why asset-first.** Each asset model is separable, has closed-form checks, and
has published validation targets. A workload layer built first would be validated
only against itself. The excitation library (WP1) is deliberately *synthetic and
analytic* — a 22 Hz sine sweep is a better first test of a torsional model than a
power trace of unknown bandwidth, and it is reproducible.

### 0.2 What v0.1 can and cannot claim

| Claim | v0.1 |
|---|---|
| "The solver is correct for these equations" | ✅ WP6 conformance suite |
| "Exposure accumulates correctly from a resolved history" | ✅ |
| "Policy A causes *N× more* exposure than policy B, same parameters" | ✅ WP8 paired ratios |
| "This asset has X% life remaining" | ❌ Blocked on field data (Doc A ch. 10) |
| "This asset will fail on date D" | ❌ Out of scope permanently |

### 0.3 Two status axes

Doc A's `EXISTS / LIMITED / PROPOSED` conflates code maturity with evidence
maturity. Split them. Both are machine-readable fields on every model output.

```python
class ImplTier(IntEnum):      NOT_STARTED=0; IMPLEMENTED=1; UNIT_TESTED=2; CI_GATED=3
class EvidenceTier(IntEnum):  PLACEHOLDER=0; LITERATURE=1; ASSET_CALIBRATED=2; FIELD_VALIDATED=3
```

`CI_GATED + PLACEHOLDER` is a legitimate and common state: working code,
meaningless numbers. Name it rather than hedging in prose.

**Enforcement (WP9).** The report generator raises `InsufficientEvidence` if an
absolute life/damage figure is requested when `evidence < ASSET_CALIBRATED`.
Ratios and exposures remain available at `LITERATURE`. This makes Doc A's claim
ladder executable instead of advisory.

---

## 1. Conventions — read before writing any code

### 1.1 Units: SI internally, always

All internal state, parameters, and interfaces use **SI base units**: s, W, V, A,
Ω, J, K, kg, m, Pa, mol. Presentation units (MW, h, °C, MPa, Ah) appear **only** in
report formatters and trace readers.

**Every variable name carries its unit as a suffix.** Non-negotiable; it is the
cheapest defect prevention in the project.

```python
oil_temp_K: float                 # not oil_temp
shaft_damping_Nms_per_rad: float  # not C_s
cell_capacity_As: float           # not capacity   (note: A·s, not Ah)
```

**Temperature rule.** Absolute temperatures in Arrhenius/kinetic expressions are
**kelvin, no exceptions**. Temperature *differences* may be K (identical to °C
difference). Public API accepts only `_K`; `_degC` exists only at I/O boundaries.
A single `°C`-into-Arrhenius error silently produces plausible wrong numbers — the
most dangerous defect class in this project, so WP6 tests for it explicitly.

### 1.2 Symbol → code map (resolves Doc A's collisions)

Doc A reuses `C_s`, `C_c`, `R`, `m`, `τ` across chapters with different meanings
and has no nomenclature table. Full table in `docs/nomenclature.md` (WP0
deliverable); the collisions that matter:

| Doc A | Meaning | SI | Code identifier |
|---|---|---|---|
| `C_s` | shaft damping | N·m·s/rad | `shaft_damping_Nms_per_rad` |
| `C_s` | cell surface heat capacity | J/K | `cell_surface_heat_capacity_J_per_K` |
| `C_c` | converter case heat capacity | J/K | `case_heat_capacity_J_per_K` |
| `C_c` | cell core heat capacity | J/K | `cell_core_heat_capacity_J_per_K` |
| `R` | governor droop | 1 | `droop_pu` |
| `R` | thermal resistance | K/W | `thermal_resistance_K_per_W` |
| `R_g` | gas constant | J/(mol·K) | `R_GAS` (module constant) |
| `m` | Basquin slope | 1 | `basquin_slope` |
| `m` | loading-guide exponent | 1 | `winding_exponent` |
| `m_j` | IGBT ΔT exponent | 1 | `coffin_manson_exponent` |
| `τ` | thermal time constant | s | `tau_s` |
| `τ_s` | shear stress | Pa | `shear_stress_Pa` |

### 1.3 Sign conventions (fixed once, enforced in tests)

| Quantity | Positive means |
|---|---|
| Battery AC power `P_ac_W` | **discharge** to site |
| Battery cell current `I_A` | **discharge** (SOC decreasing) |
| Converter power | flow in the nameplate-forward direction |
| Shaft torque `T_s_Nm` | turbine driving generator |
| Heat flow | into the named region |

### 1.4 Requirement IDs and commit discipline

`<ASSET>-<nn>` — `TR` transformer, `BT` battery, `GT` turbine, `CV` converter,
`CO` core/shared, `ST` study, `SE` state estimation.
Test vectors append a letter: `TR-04e`.

Every commit touching a model references at least one ID:
`TR-04: implement paper aging rate + vectors a–e`.
Without this link the spec and code diverge within a month.

### 1.5 Reference fixtures — read this carefully

To make tests executable today, this spec defines **four reference assets** with
complete, internally consistent parameter sets. Every number is marked:

- **`LIT`** — derivable from open literature or a public standard clause.
- **`FIXTURE`** — synthesized to be *self-consistent and plausible*, for testing only.

> **A `FIXTURE` parameter set is not a model of any real asset.** The fixtures
> exist so that WP2–WP5 have golden vectors and CI gates on day one. Every fixture
> value carries `evidence: PLACEHOLDER`, which blocks absolute life claims at
> runtime (§0.3). Replacing fixtures with asset data is the WP7 calibration path.

### 1.6 Numerical defaults

| Setting | Default | Rationale |
|---|---|---|
| Stiff ODE solver | `scipy.integrate.solve_ivp(method="Radau")` | thermal networks, index-1 coupling |
| Non-stiff / oscillatory | `method="RK45"` | torsional shaft |
| `rtol` / `atol` | `1e-8` / `1e-10` (states ~O(1–100)) | refinement headroom |
| Linear time-invariant thermal | `scipy.linalg.expm` exact step | exact, fast, no tolerance |
| Float comparison of times | `math.isclose(..., abs_tol=1e-9)` | never `==` |

**Rule.** Where a sub-model is linear time-invariant with inputs held constant over
a step, use the **exact matrix-exponential update**, not a generic integrator. It is
faster *and* removes a tolerance knob. This applies to transformer thermal (§2.4),
ECM relaxation (§3.4), and converter Cauer networks (§5.4).

---

## 2. Work packages — dependency order

```
WP0 Foundations ────┬──> WP1 Shared kernels ──┬──> WP2 Transformer ──┐
  contracts,        │      excitation,        │                      │
  units, harness    │      rainflow, Miner,   ├──> WP3 Battery ──────┤
                    │      Arrhenius, expm    │                      ├──> WP6 Conformance
                    │                         ├──> WP4 Turbine ──────┤       suite
                    │                         │                      │         │
                    │                         └──> WP5 Converter ────┘         │
                    │                                                          v
                    └────────────────────────────────> WP7 Calibration & sensitivity
                                                              │
                                                       WP8 Study runner
                                                              │
                                                       WP9 State estimation
```

| WP | Scope | Blocks on | Parallel-safe after WP1 |
|---|---|---|---|
| 0 | Contracts, units, nomenclature, tiers, harness, CI | — | — |
| 1 | Excitation library + damage/thermal kernels | WP0 | — |
| 2 | Transformer: loading → losses → 2-state thermal → paper aging | WP1 | ✅ |
| 3 | Battery: ECM → SOC → 2-state thermal → calendar+cycle aging | WP1 | ✅ |
| 4 | Turbine: torsional → shear stress → rainflow → Miner (Haibach) | WP1 | ✅ |
| 5 | Converter: losses → Foster/Cauer thermal → ΔTj cycling + capacitor | WP1 | ✅ |
| 6 | Cross-asset conformance suite (Doc A's "Do not" as tests) | WP2–5 | — |
| 7 | Parameter identification, identifiability, SALib screening | WP2–5 | ✅ per asset |
| 8 | Study runner, paired differential metrics, result schema | WP2–5 | — |
| 9 | Per-asset UKF state estimation + evidence-tier reporting | WP8 | — |

---

## WP0 — Foundations

**Goal.** Two developers can write two models that exchange a record with no unit
defect, and a golden-vector test runs in CI.

### CO-01 Package layout

Six modules for v0.1. Doc A's twenty responsibility boundaries are deferred until
the code demands them.

```
asset_life/
  contracts.py     # dataclasses, enums, unit registry, exceptions
  kernels/         # WP1: excitation, rainflow, miner, arrhenius, thermal
  models/          # WP2-5: transformer.py battery.py turbine.py converter.py
  study.py         # WP8: case runner, paired execution, metrics
  calib.py         # WP7: identification, sensitivity
  io.py            # readers, writers, report generation
tests/
  golden/          # *.csv test vectors, versioned
  conformance/     # WP6
docs/nomenclature.md
```

### CO-02 The asset model protocol

Every asset implements this. Typed, not prose.

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class AssetModel(Protocol):
    def initialize(self, params: ParamSet, state0: State | None,
                   t0_s: float) -> InitReport: ...
    def advance(self, t_target_s: float, inputs: IntervalInput,
                numerics: Numerics) -> AdvanceResult: ...
    def limits(self) -> LimitReport: ...
    def snapshot(self) -> Snapshot: ...
    def restore(self, snap: Snapshot) -> None: ...
    def summary(self) -> SummaryRecord: ...
```

**Contract rules (tested in WP6):**

1. `advance` must reach `t_target_s` exactly or raise. Never silently stop short.
2. `snapshot` → `restore` → `advance` must be **bit-identical** to an
   uninterrupted `advance`, including **unclosed rainflow residues** and all
   accumulators. This is the single hardest contract; it is `CO-02e`.
3. `advance` **must not mutate** `inputs` or `params`.
4. Out-of-range input raises `OutOfRange`; solver failure raises `SolverFailure`.
   **Neither may return zero damage.** (`CO-02f` — Doc A SW-08.)

### CO-03 Core records

```python
@dataclass(frozen=True, slots=True)
class IntervalInput:
    t_start_s: float
    t_end_s: float
    interp: Literal["hold", "linear"]      # how inputs vary within the interval
    channels: Mapping[str, float | Callable[[float], float]]

@dataclass(frozen=True, slots=True)
class AdvanceResult:
    t_reached_s: float
    outputs: Mapping[str, float]           # endpoint states
    exposure: Mapping[str, float]          # interval accumulators (additive)
    peaks: Mapping[str, float]             # interval maxima
    damage: Mapping[str, float]            # dimensionless increments
    flags: frozenset[str]
    evidence: EvidenceTier
    impl: ImplTier
```

**Why four output dicts.** Doc A's three-output separation (condition / exposure /
damage) is its best idea. Make it structural: `exposure` is additive across
intervals, `peaks` is max-reduced, `damage` is additive but gated by tier,
`outputs` is endpoint-only and **not** additive. Mixing them is the most common
error in life-estimation codebases.

### CO-04 Exceptions

```python
class AssetLifeError(Exception): ...
class OutOfRange(AssetLifeError): ...        # input outside Range field
class SolverFailure(AssetLifeError): ...
class InsufficientEvidence(AssetLifeError): ...  # absolute claim below tier
class UnitMismatch(AssetLifeError): ...
```

### CO-05 Golden vector harness

```
tests/golden/<ID>.csv      # header: input cols, expected cols, abs_tol, rel_tol
```

Runner parametrises over every file. Each carries the generating commit hash.
A new golden file requires a reviewer signature in its header comment.

### CO-06 Environment — pixi

The project already standardises on **pixi** with the `conda-forge` channel
(`pixi.toml`, `pixi.lock` at repo root). Do not introduce venv, bare conda, or
Poetry. Extend the existing manifest rather than creating a second one.

**New dependencies for v0.1** (all `conda-forge`, all permissive licences):

Versions below were resolved against `conda-forge` on 2026-10-19; re-check at
implementation time.

```toml
# append to [dependencies] in pixi.toml
salib      = ">=1.6.0,<2"     # WP7 sensitivity screening            MIT
filterpy   = ">=1.4.5,<2"     # WP9 UKF                              MIT
rainflow   = ">=3.0.1,<4"     # WP1 cross-check only, not the impl   MIT
pytest-cov = ">=7.1.0,<8"     # coverage gate                        MIT
hypothesis = ">=6.170,<7"     # property tests (CO-02e, CO-11b)      MPL-2.0
```

**Optional, isolated by pixi feature** — these must never be required to run
WP2–WP6, so they go in a separate environment to keep solver conflicts out of the
core (Doc A SW-15):

```toml
[feature.battery-detail.dependencies]
pybamm = ">=26.8,<27"         # WP7 reference studies only           BSD-3

[environments]
default        = { solve-group = "default" }
battery-detail = { features = ["battery-detail"], solve-group = "detail" }
```

**pyLife is not on conda-forge.** It is PyPI-only, so it cannot join the conda
dependency set. Options, in order of preference:

1. **Drop it.** `CO-11`/`CO-12` are ~60 lines each and fully specified here with
   golden vectors; `rainflow` (conda-forge, MIT) already provides an independent
   cross-check of the counting. This is the recommended path.
2. **Add it later** as a `[pypi-dependencies]` entry in a `fatigue-ext` feature if an
   independent S-N/Miner cross-check is wanted later.

Do not block WP1 on this decision.

**New tasks:**

```toml
# append to [tasks]
asset-test     = "pytest tests/ -v -m 'not nightly'"
asset-nightly  = "pytest tests/ -v -m nightly"
asset-golden   = "pytest tests/golden -v"
asset-conform  = "pytest tests/conformance -v"
asset-cov      = "pytest tests/ --cov=asset_life --cov-report=term-missing"
asset-sens     = "python -m asset_life.calib.screen"
asset-study    = "python -m asset_life.study"
```

**`CO-06a`** `pixi run asset-test` is green on a clean checkout with no network
access beyond the lockfile.
**`CO-06b`** `pixi run asset-test` succeeds in the `default` environment, i.e.
with PyBaMM absent (and pyLife never installed). Enforces Doc A SW-15: optional
adapters never gate the core.
**`CO-06c`** `pixi.lock` is committed and CI fails if `pixi install` would modify it.

**Licence manifest.** `docs/licenses.md` records library → licence → role →
redistribution implication. Resolve before WP8: **ANDES is copyleft (GPL-3 —
verify)** against an otherwise BSD/MIT/Apache stack. It is not needed for WP2–WP5,
so keep it behind a feature if it is kept at all. Paywalled standards (IEEE C57.91,
IEC 60076-7/-14, ASTM E1049) may be *cited by clause* and their public equations
implemented; never vendor their text or tables.

### WP0 gate

- [ ] `nomenclature.md` complete; no symbol maps to two code identifiers
- [ ] `AssetModel` protocol importable; a trivial `NullAsset` passes `CO-02a–f`
- [ ] Golden runner green on a hand-checked arithmetic fixture
- [ ] CI runs full suite in < 3 min; heavy cases marked `@pytest.mark.nightly`
- [ ] `pixi.toml` extended per `CO-06`; `pixi.lock` committed; license manifest written

---

## WP1 — Shared kernels

**Goal.** The numerics every asset needs, tested once against closed-form results.

### CO-10 Excitation library — analytic signals only

Replaces any workload model in v0.1. Every signal is deterministic, closed-form,
and has known spectral content.

| Signal | Signature | Purpose |
|---|---|---|
| `step` | `step(t, t0, lo, hi)` | steady-state and time-constant recovery |
| `ramp` | `ramp(t, t0, t1, lo, hi)` | rate-limit and ramp-response checks |
| `square` | `square(t, period_s, lo, hi, duty)` | ⟨F(T)⟩ ≠ F(⟨T⟩); thermal cycling |
| `sine` | `sine(t, f_Hz, mean, amp)` | resonance; single-frequency response |
| `chirp` | `chirp(t, f0_Hz, f1_Hz, T_s, mean, amp)` | sweep through torsional modes |
| `pulse_train` | `pulse_train(t, period_s, width_s, lo, hi)` | duty-cycle exposure |
| `prbs` | `prbs(t, bit_s, lo, hi, seed)` | broadband, reproducible, declared PSD |

**`CO-10a`** Each signal's analytic RMS and mean match the numerically integrated
value to `1e-10`.
**`CO-10b`** `chirp` instantaneous frequency is within `1e-9` of the linear law —
WP4's resonance test depends on it.

**Rationale.** A deliberately synthetic excitation with *declared* bandwidth is a
stronger first test than a measured trace of *unknown* bandwidth. Doc A's own
warning box makes this point: a 1 s power record cannot establish 22 Hz content.
WP1 sidesteps that by making bandwidth an input, not an assumption.

### CO-11 Rainflow counting (ASTM E1049 four-point)

```python
def rainflow(turning_points: Sequence[float]
             ) -> tuple[list[Cycle], list[float]]:
    """Returns (closed cycles, residue). Cycle = (range, mean, count)."""
```

**Requirements.** Residue is returned, never discarded. `rainflow_streaming`
accepts a prior residue so histories can be processed in blocks — this is what
makes `CO-02e` restart equivalence achievable.

**Golden vector `CO-11a`** — the standard sequence `[-2, 1, -3, 5, -1, 3, -4, 4, -2]`:

| range | mean | count |
|---|---|---|
| 4.00 | 1.00 | 1.0 |
| 3.00 | −0.50 | 0.5 |
| 4.00 | −1.00 | 0.5 |
| 8.00 | 1.00 | 0.5 |
| 9.00 | 0.50 | 0.5 |
| 8.00 | 0.00 | 0.5 |
| 6.00 | 1.00 | 0.5 |

Total count **4.0**; residue `[-2, 1, -3, 5, -4, 4, -2]`.
*Cross-check this table against ASTM E1049-85 §5.4.4 before signing off.*

**`CO-11b`** Block-processed counting (any split) equals single-pass counting.
**`CO-11c`** Turning-point extraction is idempotent.

### CO-12 Miner accumulation with cutoff — mandatory

```python
def miner_sum(cycles, sn_curve: SNCurve) -> float
```

**`SNCurve` must implement one of three regimes beyond the knee.** Doc A has no
endurance limit, no knee, no Haibach — zero mentions. Without a cutoff the Miner
sum is a function of the sample rate and filter window, not of physics.

| Mode | Beyond knee `N_k` |
|---|---|
| `"cutoff"` | `N_f = inf` (no damage below `S_k`) |
| `"haibach"` | slope `2m − 1` |
| `"none"` | pure Basquin — **raises unless explicitly opted in** |

**Golden vector `CO-12a`** (`N_ref=1e6`, `S_a,ref=200 MPa`, `m=8`, `N_k=1e7`):

- Knee amplitude `S_k = 149.978842 MPa`
- Haibach slope beyond knee `= 15.0`
- 10⁹ cycles at `0.5·S_k = 74.989421 MPa`:

| mode | `n/N_f` |
|---|---|
| `none` (Basquin) | `3.906250e-01` |
| `haibach` | `3.051758e-03` |
| `cutoff` | `0.0` |

**`none` and `haibach` differ by 128×.** This is the single most important test
in WP1: it demonstrates that the headline damage number is dominated by a
modelling choice Doc A never makes.

### CO-13 Arrhenius / rate accumulators

```python
def arrhenius_ratio(T_K, T_ref_K, B_K) -> float            # exp[B(1/Tref - 1/T)]
def arrhenius_Ea(T_K, T_ref_K, Ea_J_per_mol) -> float      # B = Ea/R_g
def accumulate_rate(rate_fn, t0_s, t1_s, T_of_t, rtol) -> float
```

**`CO-13a`** `arrhenius_ratio(T_ref, T_ref, B) == 1.0` exactly (`abs_tol 1e-12`).
**`CO-13b`** Passing °C where K is expected is **detectable**: values < 200 raise
`OutOfRange`. Catches the highest-consequence silent defect in the project.
**`CO-13c`** `accumulate_rate` over a square wave ≠ rate at mean temperature
(quantified per asset; see `TR-04e`).

### CO-14 Linear thermal network kernel

```python
def lti_thermal_step(A, b, x0, h_s) -> NDArray      # expm, exact
def cauer_from_foster(R, tau) -> tuple[A, b]        # with documented caveat
```

**`CO-14a`** Steady state from `expm` matches `solve(-A, b)` to `1e-10`.
**`CO-14b`** Exact step matches `Radau` at `rtol=1e-10` to `1e-8`.
**`CO-14c`** `cauer_from_foster` raises unless the caller passes
`acknowledge_nonphysical_nodes=True` — see §5.4.

### WP1 gate

- [ ] `CO-10a/b`, `CO-11a/b/c`, `CO-12a`, `CO-13a/b/c`, `CO-14a/b/c` green
- [ ] `rainflow` streaming equivalence holds for 100 random split points
- [ ] No kernel imports any model; models import kernels only

---

## WP2 — Transformer

**Status:** `impl: NOT_STARTED` · `evidence: LITERATURE` (thermal/aging), `PLACEHOLDER` (fixture parameters)

Most literature-supportable of the four assets. Build it first; it is the template.

### 2.1 Inputs and outputs

**Inputs (per interval):**

| Channel | Unit | Source | Required |
|---|---|---|---|
| `load_current_A` *or* `load_power_W` + `pf` | A / W | excitation or trace | ✅ |
| `ambient_temp_K` | K | excitation or trace | ✅ |
| `cooling_mode` | enum `ONAN/ONAF/OFAF` | discrete input | ✅ |
| `harmonic_spectrum` | `{h: I_h_A}` | waveform capture | optional |

**Outputs:**

| Dict | Key | Unit |
|---|---|---|
| `outputs` | `winding_temp_K`, `oil_temp_K`, `hotspot_temp_K`, `loss_total_W`, `load_ratio_K` | K, W, 1 |
| `exposure` | `equivalent_aging_hours_h`, `loss_energy_J`, `hours_above_limit_h` | h, J, h |
| `peaks` | `hotspot_temp_max_K`, `load_ratio_max` | K, 1 |
| `damage` | `paper_aging_fraction` (tier-gated) | 1 |
| `flags` | `HOTSPOT_LIMIT`, `OVERLOAD`, `COOLING_UNAVAILABLE` | — |

### 2.2 Reference fixture — 2.5 MVA ONAN unit

| Parameter | Value | Unit | Tier |
|---|---|---|---|
| `rating_VA` | 2.5e6 | VA | FIXTURE |
| `no_load_loss_W` `P_0` | 2500 | W | FIXTURE |
| `dc_loss_rated_W` | 20000 | W | FIXTURE |
| `eddy_loss_rated_W` | 2000 | W | FIXTURE |
| `stray_loss_rated_W` | 1000 | W | FIXTURE |
| `top_oil_rise_rated_K` | 45 | K | **LIT** (IEC 60076-2 typical ONAN) |
| `winding_gradient_rated_K` | 20 | K | **LIT** (typical gradient) |
| `hotspot_rise_rated_K` | 36 | K | **LIT** (→ 81 K total rise, IEC class) |
| `tau_oil_s` | 10800 | s | **LIT** (3 h, IEC 60076-7 range 2.5–3.5 h) |
| `tau_winding_s` | 240 | s | **LIT** (4 min, IEC 60076-7 range 4–10 min) |

**Derived (verify by recomputation — `TR-01a`):**

```
P_loss,R    = 2500 + 20000 + 2000 + 1000      = 25500 W
P_w,R       = dc + eddy  (winding region)      = 22000 W
P_o,R       = P_loss,R - P_w,R                 =  3500 W
G_oa        = P_loss,R / 45                    =   566.6667 W/K
G_wo        = P_w,R / 20                       =  1100.0000 W/K
C_o         = G_oa * tau_oil                   = 6.120000e6 J/K
C_w         = G_wo * tau_winding               = 2.640000e5 J/K
k_H         = (81 - 45) / 20                   =     1.8000
```

### 2.3 TR-01 … TR-03 Loading and losses

**TR-01 Load ratio.** `K = I_rms / I_R`. With harmonics `I_rms² = Σ_h I_h²`.
Balanced sinusoidal: `I_rms = P / (√3 · V_LL · pf)`.

**TR-02 Harmonic loss factors.**
```
F_E = Σ h² I_h² / Σ I_h²        (winding eddy)
F_S = Σ h^0.8 I_h² / Σ I_h²     (stray)
```
`Source` IEEE C57.110 — **verify clause**. `Range` h ≤ 50; raise above.
Zero denominator → return `F_E = F_S = 1.0` and zero load loss.

**TR-03 Total loss.**
```
P_loss = P_0 + K² [ P_dc,R + F_E·P_eddy,R + F_S·P_stray,R ]
```

**Golden vector `TR-03a`** — 6-pulse spectrum, `I_h/I_1 = {1:1.0, 5:0.20, 7:0.14, 11:0.09, 13:0.07}`:

| Quantity | Value |
|---|---|
| `I_rms` | `1.03566404` pu |
| THD | `26.9444 %` |
| `F_E` | `4.44583256` |
| `F_S` | `1.24111350` |
| `P_loss` at `K = I_rms` | `34820.4183 W` |

Compare with `25500.0 W` sinusoidal at `K=1`: **+36.5 % loss** from 27 % THD.
`TR-03b`: asserts that applying a single factor to *all* losses (including `P_0`)
gives a different, wrong answer — Doc A's "do not multiply all losses by one
harmonic factor", executable.

### 2.4 TR-05 Two-state thermal model

```
C_w dT_w/dt = P_w − G_wo (T_w − T_o)
C_o dT_o/dt = P_o + G_wo (T_w − T_o) − G_oa (T_o − T_A)
T_H         = T_o + k_H (T_w − T_o)
```

**Solution procedure.** Linear time-invariant for fixed `P_w, P_o, T_A, cooling_mode`,
so use the **exact matrix exponential**, not an integrator:

```python
A = [[-G_wo/C_w,            G_wo/C_w          ],
     [ G_wo/C_o,  -(G_wo+G_oa)/C_o            ]]
b = [ P_w/C_w,  (P_o + G_oa*T_A)/C_o ]
x(t+h) = expm(A*h) @ x(t) + solve(A, (expm(A*h) - I) @ b)
```

Split the interval at every cooling-mode change, load discontinuity, and event
time. For `interp="linear"` inputs, subdivide into ≤ 60 s sub-steps and hold
piecewise-constant (document the bias; `TR-05f` bounds it).

**Golden vectors `TR-05a–e`** — fixture at rated load, `T_A = 293.15 K (20 °C)`,
both states starting at ambient. Temperatures in °C for readability; code uses K.

| t (s) | `T_w` (°C) | `T_o` (°C) | `T_H` (°C) |
|---|---|---|---|
| 60 | 24.42989 | 20.05876 | 27.92680 |
| 240 | 32.77992 | 20.44472 | 42.64808 |
| 600 | 39.28855 | 21.63018 | 53.41525 |
| 3600 | 50.99236 | 31.71618 | 66.41329 |
| 10800 | 67.04153 | 47.42376 | 82.73575 |
| 43200 | 83.98519 | 64.00678 | 99.96791 |

**Steady state:** `T_w = 85.0000`, `T_o = 65.0000`, `T_H = 101.0000 °C`
(= 20 + 45 + 36, recovering the rated rises exactly — this is `TR-05a` and it
validates the whole parameter derivation chain).

**Eigenvalues:** `−4.35031400e-03`, `−8.86838212e-05` s⁻¹
→ time constants `229.8685 s`, `11276.0139 s`.
Note these are *not* equal to `tau_winding` and `tau_oil`: the coupled system's
modes differ from the uncoupled constants. `TR-05d` asserts this explicitly, since
confusing the two is a standard error.

**`TR-05e`** Step `T_A` by 10 K → both temperatures move continuously, no jump.
**`TR-05f`** Halving the sub-step changes `equivalent_aging_hours_h` by < 0.1 %.

### 2.5 TR-04 Paper aging

```
F_age(T_H) = exp[ B (1/T_ref − 1/T_H) ]        T in K
L_eq       = (1/3600) ∫ F_age(T_H(t)) dt       h
```

| Field | Value |
|---|---|
| Default | `B = 15000 K`, `T_ref = 383.15 K (110 °C)` |
| Source | IEEE C57.91 thermally-upgraded kraft — **verify clause** |
| Range | `B ∈ [14000, 16000]`; `T_H ∈ [320, 420] K`; raise outside |
| Code | `paper_aging_rate(hotspot_temp_K: float) -> float` |

**Golden vectors `TR-04a–e`:**

| `T_H` (°C) | `F_age` |
|---|---|
| 80 | `0.03594696` |
| 95 | `0.20288752` |
| 98 | `0.28202254` |
| 110 | `1.00000000` |
| 120 | `2.70683950` |
| 125 | `4.37066389` |
| 140 | `17.16259070` |

- `TR-04a` `F_age(383.15 K) == 1.0` (`abs_tol 1e-12`)
- `TR-04c` 2 h at 120 °C → `L_eq = 5.413679 h`
- `TR-04d` `T_H = 300 K` raises `OutOfRange`
- **`TR-04e`** `T_H = 110 ± 15 °C` square wave, 50 % duty:
  `⟨F(T)⟩ = 2.286776` vs `F(⟨T⟩) = 1.000000` — **ratio 2.2868**.
  Doc A's "do not average away damage", as a number. Aging at the mean
  temperature understates by 2.29× for a ±15 K swing.

**Reference loss of life** requires `L_ref` and is **tier-gated**: with
`L_ref = 180000 h`, 1 h at 120 °C = `1.50379972e-03 %`. Reported only at
`evidence ≥ ASSET_CALIBRATED`; below that, `L_eq` only.

### 2.6 Deliberately excluded: oil oxidation chemistry

Doc A §3.6 proposes a three-state oil model with ≥ 9 fitted coefficients
(`A, E_a, K_O, γ, ν_a, ν_b, ν_c, k_rem, k_air`) against 4–12 oil samples/year.
**That is unidentifiable by roughly an order of magnitude** and is excluded from
v0.1. WP7 provides the identifiability test that demonstrates it quantitatively,
which is the honest basis for the exclusion.

Report measured oil indicators as *data*, never as model output.

### WP2 gate

- [ ] `TR-01a` parameter derivation reproduces all seven derived values
- [ ] `TR-03a/b`, `TR-04a–e`, `TR-05a–f` green
- [ ] Steady state recovers rated rises to `1e-6 K`
- [ ] `expm` path matches `Radau` at tight tolerance (`CO-14b`) for this asset
- [ ] `snapshot/restore` round-trip bit-identical including `L_eq` accumulator

---

## WP3 — Battery

**Status:** `impl: NOT_STARTED` · `evidence: LITERATURE` (ECM/SOC), `PLACEHOLDER` (aging)

### 3.1 Inputs and outputs

**Inputs:** `power_ac_W` (+ = discharge) **or** `current_A`; `coolant_temp_K`;
`cooling_mode`. **Outputs:** `outputs` → `soc`, `cell_voltage_V`, `cell_current_A`,
`v_p_V`, `core_temp_K`, `surface_temp_K`, `capacity_As`, `resistance_ohm`;
`exposure` → `charge_throughput_As`, `efc`, `hours_above_soc_limit_h`;
`peaks` → `core_temp_max_K`, `c_rate_max`; `damage` → `capacity_loss_fraction`,
`resistance_growth_fraction` (both tier-gated);
`flags` → `POWER_UNAVAILABLE`, `SOC_LIMIT`, `VOLTAGE_LIMIT`, `TEMP_LIMIT`.

### 3.2 Reference fixture — LFP, 100 Ah, 240s × 10p

| Parameter | Value | Unit | Tier |
|---|---|---|---|
| `cell_capacity_Ah` | 100 | Ah | FIXTURE |
| `n_series` / `n_parallel` | 240 / 10 | — | FIXTURE |
| `U_oc_V` (at `z = 0.5`) | 3.30 | V | **LIT** (LFP plateau) |
| `R_0_ohm` | 6.0e-4 | Ω | FIXTURE |
| `R_p_ohm` | 4.0e-4 | Ω | FIXTURE |
| `C_p_F` | 5.0e4 | F | FIXTURE (`R_p·C_p = 20 s`) |
| `eta_discharge` / `eta_charge` | 0.97 / 0.97 | — | FIXTURE |

Pack: `792.0 V`, `1000 Ah`, **`792.000 kWh`** nominal.

> LFP chosen because its flat OCV plateau makes a constant-`U_oc` fixture
> defensible for v0.1 tests. A real `U_oc(z,T)` curve is a WP7 deliverable.

### 3.3 BT-01 Power → cell current (numerically stable)

```
P_dc = P_ac/eta_dis  (P_ac ≥ 0)   |   eta_ch·P_ac  (P_ac < 0)
P_c  = P_dc / (N_s·N_p)
U_e  = U_oc − v_p
```
Solve `U_e·I − R_0·I² = P_c` for the low-current root. **Do not use the naive
form** — use the numerically stable equivalent:

```python
disc = U_e**2 - 4*R_0*P_c
if disc < 0: raise PowerUnavailable(...)      # never silently clip
I = 2*P_c / (U_e + sqrt(disc))                # stable
# NOT: I = (U_e - sqrt(disc)) / (2*R_0)       # catastrophic cancellation at small P
```

**Golden vector `BT-01a`** — `P_ac = 500 kW` discharge, `v_p = 0`:

| Quantity | Value |
|---|---|
| `P_dc` | `515463.918 W` |
| `P_cell` | `214.776632 W` |
| discriminant | `10.37453608` |
| `I_cell` | `65.87277758 A` |
| C-rate | `0.658728 1/h` |
| `V_cell` | `3.26047633 V` |

**`BT-01b`** Both forms agree here (diff `1.137e-13`), but as `P_c → 0` the naive
form loses precision while the stable form does not. Test at `P_c = 1e-12 W`.
**`BT-01c`** Model max cell power `U_oc²/(4R_0) = 4537.5000 W`
→ pack `10.8900 MW` DC. Above this `disc < 0` and `PowerUnavailable` raises.
**`BT-01d`** `PowerUnavailable` must propagate as an unmet-power flag, never as
a silently clipped current with an unbalanced interval.

### 3.4 BT-02 ECM relaxation — exact update

```
v_p(t+h) = R_p·I + (v_p(t) − R_p·I)·exp(−h/(R_p·C_p))
```

**Golden vector `BT-02a`** — `I = 65.9 A` step from `v_p = 0`, `R_p·C_p = 20.0 s`:

| h (s) | `v_p` (V) |
|---|---|
| 0.1 | `0.0001314710` |
| 1.0 | `0.0012855924` |
| 10.0 | `0.0103718518` |
| 20.0 | `0.0166626979` |
| 100.0 | `0.0261823877` |

`v_p(∞) = R_p·I = 0.026360 V`. At `h = R_p·C_p` the response is `63.2 %` of final.

### 3.5 BT-03 State of charge

```
dz/dt = −I_eff / (3600·Q_avail_Ah)        I_eff = I (I≥0), eta_Q·I (I<0)
```

**`BT-03a`** 50 A for 600 s from `z = 0.80`, `Q = 100 Ah`:
`ΔAh = 8.333333`, `Δz = 0.08333333`, `z = 0.71666667`.
Note: **8.33 percentage points of SOC, not 8.33 % capacity loss.**
`BT-03b` asserts the distinction by checking `capacity_As` is unchanged.

**`BT-03c`** Full discharge + full charge of 100 Ah → `EFC = 1.000000` where
`N_EFC = ∫|I|dt / (2·Q_begin·3600)`.
**`BT-03d`** When `Q_avail` changes by aging, charge inventory must be conserved:
no energy invented or destroyed by the capacity update.

### 3.6 BT-04 Two-state cell thermal

```
C_c dT_c/dt = q_gen − G_cs (T_c − T_s)
C_s dT_s/dt = G_cs (T_c − T_s) − G_sf (T_s − T_f)
q_irr = I²R_0 + v_p²/R_p
q_rev = −I·T·(∂U_oc/∂T)
```

**`BT-04a`** Zero current, `T_c = T_s = T_f` → all derivatives zero.
**`BT-04b`** `q_irr ≥ 0` always, both current directions.
**`BT-04c`** Energy check: `∫q_gen dt` equals stored + rejected to `1e-9` relative.
**`BT-04d`** `I·v_p ≠ q` at an instant — the `R_p C_p` branch stores energy.
Doc A flags this; make it a test.
**`BT-04e`** `q_rev` flips sign with current direction; `∂U_oc/∂T = 0` → term vanishes.

### 3.7 BT-05 Aging — explicit double-counting guard

Calendar:
```
da_Q/dt = g_Q(z,T)/t_ref ;  g_Q = g_z(z)·exp[(E_Q/R_g)(1/T_ref − 1/T)]
q_cal   = k_Q · a_Q^beta_Q
```
Cycle: rainflow over the **SOC** history (reusing `CO-11`) → `Δq_cyc = Σ n_i w_Q(d_i, z̄_i, T̄_i, C_ch, C_dis)`.

```
Q_avail = Q_begin (1 − q_cal − q_cyc)
R_aged  = R_begin (1 + r_cal + r_cyc)
```

| Field | Value |
|---|---|
| Default | **none — `PLACEHOLDER`, must be supplied** |
| Source | BLAST-Lite (NREL, BSD-3) or Severson/CALCE/NASA open datasets |
| Range | model-specific; raise outside fitted SOC/T/C-rate box |
| Code | `calendar_loss(a_Q)`, `cycle_loss(cycles)` |

**`BT-05a`** Exposure-state integration: `a_Q` accumulates, then `^beta_Q` is
applied. Adding `k_Q(Δt)^beta_Q` per step gives **step-dependent** aging — assert
the two differ and that the implementation uses the former.
**`BT-05b`** `a_Q` is in the snapshot; restart equivalence holds across a split.
**`BT-05c`** Combining a cycle model that already includes test-duration calendar
aging with a separate calendar term raises unless
`acknowledge_overlap=True`. Doc A's "do not count that aging again", enforced.
**`BT-05d`** Capacity and resistance use **separate** activation energies and
exponents; a shared-parameter shortcut raises.
**`BT-05e`** Outside the calibrated health range → `flags |= {"OUT_OF_RANGE"}`
and `damage` is omitted, not extrapolated.

### WP3 gate

- [ ] `BT-01a–d`, `BT-02a`, `BT-03a–d`, `BT-04a–e`, `BT-05a–e` green
- [ ] Charge and energy balance close to `1e-9` relative over a 24 h cycle case
- [ ] Unmet power is reported, never silently clipped
- [ ] Core runs with **no PyBaMM installed** (`CO-06b`)

---

## WP4 — Turbine (torsional shaft)

**Status:** `impl: IMPLEMENTED` (existing repo code) · `evidence: PLACEHOLDER`

> **Wrap, do not rewrite.** The repo contains working multishaft + torsional code
> with its own V&V history (`docs/vv_log.md`). WP4 is an **adapter** to `AssetModel`
> plus the two things the existing code lacks: a fatigue cutoff and restart
> equivalence. Preserving the existing V&V results behind the new interface is the
> gate.

### 4.1 Inputs and outputs

**Inputs:** `power_demand_W`, `speed_ref_pu`, event times.
**Outputs:** `outputs` → `shaft_torque_Nm`, `shear_stress_Pa`, `twist_rad`,
`speed_pu`, `valve_position`; `exposure` → `cycle_counts` (binned), `rainflow_residue`,
`operating_hours_h`; `peaks` → `shear_stress_max_Pa`, `torque_max_Nm`;
`damage` → `miner_sum` (tier-gated, **cutoff mode recorded**);
`flags` → `TRIP`, `FREQ_LIMIT`, `RESONANCE_RISK`.

### 4.2 Reference fixture — LM2500-class shaft

| Parameter | Value | Unit | Tier |
|---|---|---|---|
| `outer_diameter_m` | 0.150 | m | FIXTURE (existing default) |
| `inner_diameter_m` | 0.050 | m | FIXTURE |
| `natural_freq_Hz` | 22.0 | Hz | FIXTURE (existing default) |
| `damping_ratio` | 0.01 | — | FIXTURE |
| `ultimate_strength_Pa` | 800e6 | Pa | FIXTURE (generic alloy steel) |
| `basquin_slope` | 8.0 | — | FIXTURE |
| `N_ref` / `S_a,ref` | 1e6 / 200e6 | cycles / Pa | FIXTURE |
| `knee_cycles` | 1e7 | cycles | **LIT** (steel knee 1e6–1e7) |

> Every mechanical number here is `FIXTURE`. Doc A is right that this chain cannot
> support asset life claims. v0.1 therefore reports **cycle exposure and damage
> *ratios*** only — never absolute shaft life. See `GT-05`.

### 4.3 GT-01 Torsional dynamics

```
J_t dΩ_t/dt = T_in − T_s
J_g dΩ_g/dt = T_s − T_e
dθ/dt       = Ω_t − Ω_g
T_s         = K_s θ + C_s (Ω_t − Ω_g)

J_red = J_t J_g/(J_t+J_g);  K_s = (2π f_n)² J_red;  C_s = 2ζ(2π f_n) J_red
```

**Solution procedure.** `RK45`, `max_step ≤ 1/(20 f_n)` = 2.27 ms at 22 Hz.
Output sample rate ≥ `10 f_n` = 220 Hz, default 1000 Hz.

**`GT-01a`** Free decay from `θ_0` oscillates at `f_n` within 0.1 %; logarithmic
decrement matches `ζ` within 1 %.
**`GT-01b`** Two parameter sets (physical-unit vs per-unit) give identical torque.
Doc A warns they must not be mixed; test the equivalence and the guard.
**`GT-01c`** Sine excitation at `f_n` → amplification ≈ `1/(2ζ) = 50`.
**`GT-01d`** `chirp` sweep 5 → 40 Hz locates the peak at `22.0 ± 0.1 Hz`.
**This is the test that motivated the excitation library.**

### 4.4 GT-02 Torque → shear stress

```
W_t   = π(D_o⁴ − D_i⁴)/(16 D_o)
tau_s = T_s / W_t
```

**Golden vector `GT-02a`** — `D_o = 0.150`, `D_i = 0.050 m`:
`W_t = 6.5449846950e-04 m³`

| `T_s` (kN·m) | `tau_s` (MPa) |
|---|---|
| 50 | `76.394373` |
| 100 | `152.788745` |
| 200 | `305.577491` |

**`GT-02b`** Solid shaft (`D_i = 0`) → `W_t = πD_o³/16`.
**`GT-02c`** Stress-concentration factor defaults to `1.0` and emits
`flags |= {"NO_SCF_EVIDENCE"}` — a smooth-shaft result must not look calibrated.

### 4.5 GT-03 Mean-stress correction

```
S_a,eq = S_a / (1 − S_m/S_su),     S_su = 0.6 S_u
```
**`GT-03a`** `S_u = 800 MPa` → `S_su = 480 MPa`; `S_a = 100`, `S_m = 150 MPa`
→ `S_a,eq = 145.454545 MPa`.
**`GT-03b`** `S_m → S_su` → raise, do not clip silently. The existing code clips
for numerical protection; the adapter must surface it as a flag.
**`GT-03c`** `S_m = 0` → `S_a,eq = S_a` exactly.

### 4.6 GT-04 S-N curve and Miner — cutoff is mandatory

```
N_f = N_ref (S_a,ref / S_a,eq)^m          S_a,eq ≥ S_k
      N_k   (S_k / S_a,eq)^(2m−1)         S_a,eq < S_k   [haibach]
      inf                                 S_a,eq < S_k   [cutoff]
D   = Σ n_i / N_f,i
```

**Golden vectors `GT-04a–c`** (fixture, `N_k = 1e7` → `S_k = 149.978842 MPa`):

| `S_a` (MPa) | `N_f` (Basquin) |
|---|---|
| 300 | `3.901844e+04` |
| 200 | `1.000000e+06` |
| 150 | `9.988721e+06` |
| 100 | `2.560000e+08` |
| 50 | `6.553600e+10` |

**`GT-04c`** 10⁹ cycles at `0.5 S_k = 74.989421 MPa`:

| mode | `n/N_f` |
|---|---|
| `none` | `3.906250e-01` |
| `haibach` | `3.051758e-03` |
| `cutoff` | `0.0` |

**128× spread.** The reported `miner_sum` **must** carry the mode in its record.
A damage number without a declared cutoff mode is meaningless, and
`CO-12` makes `none` opt-in for exactly this reason.

### 4.7 GT-05 Filter and sample-rate sensitivity — the headline test

The existing demo subtracts a 5 s moving median to split slow from fast torque.
**`GT-05a`** Vary the median window (2, 5, 10, 20 s) at fixed input → record the
spread in `miner_sum`. **`GT-05b`** Vary the output sample rate (220, 500, 1000,
5000 Hz) → record the spread.

**Acceptance is not "the spread is small".** Acceptance is that the spread is
**measured, reported in the result record, and smaller than any claimed difference
between two operating policies.** If the spread exceeds the policy difference, the
study must report "cannot distinguish". This is the most important single test in
WP4 and it directly addresses the arbitrariness identified in the Doc A review.

### 4.8 GT-06 Deferred

Hot-part metal temperature, thermal stress, creep: **not in v0.1**. Doc A's own
risk table makes these unfundable without OEM access. Emit
`flags |= {"HOT_PART_NOT_MODELLED"}` so no consumer mistakes shaft damage for
turbine life.

### WP4 gate

- [ ] Existing `docs/vv_log.md` results reproduced **through the adapter**, unchanged
- [ ] `GT-01a–d`, `GT-02a–c`, `GT-03a–c`, `GT-04a–c` green
- [ ] `GT-05a/b` spreads measured and written into the result record
- [ ] Rainflow residue preserved across interval boundaries (`CO-02e`)
- [ ] No absolute life claim reachable at `evidence: PLACEHOLDER`

---

## WP5 — Converter

**Status:** `impl: NOT_STARTED` · `evidence: LITERATURE` (loss/thermal), `PLACEHOLDER` (life)

### 5.1 Inputs and outputs

**Inputs:** `power_W`, `dc_voltage_V`, `ac_current_A`, `switching_freq_Hz`,
`coolant_temp_K`, `mode` (`double_conversion` / `eco` / `bypass` / `charge` / `discharge`).
**Outputs:** `outputs` → `loss_total_W`, `loss_conduction_W`, `loss_switching_W`,
`junction_temp_K`, `case_temp_K`, `heatsink_temp_K`, `cap_temp_K`;
`exposure` → `junction_cycles` (binned), `cap_exposure`, `loss_energy_J`;
`peaks` → `junction_temp_max_K`; `damage` → `igbt_miner_sum`, `cap_damage` (tier-gated);
`flags` → `TEMP_LIMIT`, `OVERLOAD`, `MODE_UNSUPPORTED`.

> **UPS operating mode is a first-class input.** Doc A never mentions eco-mode vs
> double-conversion, yet it dominates loss and junction cycling. Mode belongs in
> the interface from day one.

### 5.2 Reference fixture — 500 kVA module

| Parameter | Value | Unit | Tier |
|---|---|---|---|
| `V_0_V` / `r_on_ohm` | 0.9 / 2.0e-3 | V / Ω | FIXTURE |
| `E_on`/`E_off`/`E_rec` (J/event) | 4.2e-3 / 5.1e-3 / 2.8e-3 | J | FIXTURE |
| `switching_freq_Hz` | 4000 | Hz | FIXTURE |
| Foster `R_l` (K/W) | 0.020, 0.030, 0.040, 0.050 | K/W | FIXTURE |
| Foster `tau_l` (s) | 5e-4, 5e-3, 5e-2, 5e-1 | s | FIXTURE |
| `cap_L_ref_h` at 85 °C | 1e5 | h | **LIT** (film cap typical) |
| `cap_voltage_exponent` `n_c` | 5.0 | — | **LIT** (film, range 3–7) |

### 5.3 CV-01/02 Losses

```
P_cond = V_0·I_avg,on + r_on·I²_rms,on
P_sw   = f_sw (E_on + E_off + E_rec)
```
**`CV-01a`** `I_avg = 120 A`, `I_rms = 180 A` → `P_cond = 172.8000 W`.
**`CV-01b`** `f_sw = 4000 Hz` → `P_sw = 48.4000 W`.
**`CV-01c`** Applying an on-time fraction when `I_rms,on` already includes
off-intervals double-counts — assert the two differ and that the implementation
documents its convention.
**`CV-01d`** `E_rec` already folded into `E_on` by some datasheets; adding both
raises unless `acknowledge_recovery_in_eon=False` is explicit.

### 5.4 CV-03 Thermal network — the Foster trap

```
Z_th(t) = Σ_l R_l [1 − exp(−t/tau_l)]
```

**Golden vector `CV-03a`** (fixture, `ΣR = 0.1400 K/W`):

| t (s) | `Z_th` (K/W) |
|---|---|
| 1e-4 | `0.0043093438` |
| 1e-3 | `0.0236233249` |
| 1e-2 | `0.0541807777` |
| 1e-1 | `0.0936500510` |
| 1.0 | `0.1332332358` |
| 10.0 | `0.1399999999` |

**`CV-03b`** A Foster network's internal nodes are **not physical temperatures**.
Attaching node `l` to a heatsink node must raise unless
`acknowledge_nonphysical_nodes=True` (`CO-14c`). Doc A identifies this trap; this
is its executable form, and it is the most commonly violated rule in converter
thermal modelling.
**`CV-03c`** Where a physical boundary is needed, convert Foster → Cauer and
assert the Cauer chain reproduces `Z_th(t)` to `1e-10` at all vectors above.

### 5.5 CV-04 Junction cycling

```
N_f = A_j (ΔT_j/ΔT_ref)^(−m_j) · exp[(E_j/R_g)(1/T̄_j − 1/T_ref)] · (t_hot/t_ref)^(−n_j)
```
`Source` LESIT / CIPS08 (Bayerer) — **the open, citable IGBT lifetime models**.
Doc A names neither; they are the project's open path for this asset. Coefficients
are published; cite and record them.

**`CV-04a`** Rainflow over the `T_j` history (reusing `CO-11`), not per switching event.
**`CV-04b`** At 4 kHz switching the junction `ΔT` per switching cycle is negligible
versus the fundamental-frequency swing — assert the switching-rate cycles
contribute < 1 % and are correctly excluded.
**`CV-04c`** Conversely, 100/120 Hz ripple cycles **must not** be filtered out:
assert their contribution is retained. Doc A warns in both directions; test both.

### 5.6 CV-05 Capacitor life

```
L(T,V) = L_ref · exp[(E_c/R_g)(1/T − 1/T_ref)] · (V_ref/V)^n_c
D_cap  = ∫ dt / (3600 L(T_cap(t), V(t)))
```

**`CV-05a`** `E_c` from a stated 10 K halving near 85 °C:
`E_c = 75984.5405 J/mol` (`0.787527 eV`-equivalent).
This is a *derivation from a stated assumption*, not a universal law —
`CV-05d` asserts the 10 K rule is **not** hard-coded.

**Golden vector `CV-05b`** (`L_ref = 1e5 h` at 85 °C, `V = V_ref`):

| `T_cap` (°C) | `L` (h) |
|---|---|
| 55 | `1030795.6321` |
| 65 | `452348.9269` |
| 75 | `208124.4562` |
| 85 | `100000.0000` |
| 95 | `50000.0000` |
| 105 | `25933.5042` |

**`CV-05c`** `L(85 °C, 0.8 V_ref) = 305175.7812 h` — voltage derating at `n_c = 5`
gives `(1/0.8)^5 = 3.0518×`.
**`CV-05e`** If ESR grows, loss and temperature must be recomputed; holding the
original loss forever is a defect (Doc A says so explicitly).

### WP5 gate

- [ ] `CV-01a–d`, `CV-03a–c`, `CV-04a–c`, `CV-05a–e` green
- [ ] Foster/Cauer equivalence to `1e-10`; non-physical-node guard active
- [ ] All five operating modes produce distinct, documented loss behaviour
- [ ] No junction life claim reachable at `evidence: PLACEHOLDER`

---

## WP6 — Cross-asset conformance suite

**Goal.** Convert Doc A's ~112 "Do not …" sentences into executable assertions.
This WP is where the review's central recommendation lands: **the caveats become
the test suite.** Target ≈ 40 convertible; the rest stay in Doc A as prose.

| ID | Doc A instruction | Assertion |
|---|---|---|
| `CF-01` | Do not average away damage | `⟨I²⟩ ≠ ⟨I⟩²` and `⟨F(T)⟩ ≠ F(⟨T⟩)`, quantified per asset |
| `CF-02` | Do not add losses twice | Boundary audit: each loss appears in exactly one owner |
| `CF-03` | Do not multiply all losses by one harmonic factor | `TR-03b` |
| `CF-04` | Do not mix per-unit and physical shaft parameters | `GT-01b` |
| `CF-05` | Do not treat every switching action as a `T_j` cycle | `CV-04b` |
| `CF-06` | Do not attach Foster nodes to a heatsink | `CV-03b` |
| `CF-07` | Do not count the same aging twice | `BT-05c`, `CV-01d` |
| `CF-08` | Do not reset SOC arbitrarily | `BT-03d` |
| `CF-09` | Do not silently clip battery current | `BT-01d` |
| `CF-10` | Failure must never read as zero damage | `CO-02f`, all assets |
| `CF-11` | Keep unclosed cycles across intervals | `CO-02e`, `CO-11b` |
| `CF-12` | One owner per physical state | static check: no state in two models |
| `CF-13` | Do not substitute °C into Arrhenius | `CO-13b`, all assets |
| `CF-14` | Do not extrapolate outside the fitted range | `BT-05e`, all assets |
| `CF-15` | Oil replacement does not reset paper age | transformer maintenance event test |

### CF-20 Universal per-asset contract tests

Parametrised over all four models — this is how WP6 stays cheap to extend:

1. **Zero input** → zero exposure, zero damage, states at equilibrium
2. **Step refinement** → halving the step changes exposure by < 0.1 %
3. **Restart equivalence** → split at 7 random points, bit-identical to one pass
4. **Monotonicity** → higher load/temperature never *decreases* damage
5. **Unit round-trip** → every `_K`/`_degC`, `_As`/`_Ah` boundary conversion is lossless
6. **Immutability** → `advance` does not mutate `inputs` or `params`
7. **Tier gating** → absolute claim at `PLACEHOLDER` raises `InsufficientEvidence`

### WP6 gate

- [ ] ≥ 40 Doc A instructions mapped to passing tests; mapping table in repo
- [ ] `CF-20` 1–7 green for all four assets
- [ ] Coverage ≥ 85 % on `asset_life/models` and `asset_life/kernels`
- [ ] Each Doc A "Do not" is either tested, or explicitly listed as prose-only with a reason

---

## WP7 — Calibration, identifiability, sensitivity

**Goal.** Know which parameters matter **before** asking a site owner for data.
Reordering this ahead of the field-data request is the highest-leverage change in
the project plan: Doc A asks for ~40 channels without knowing which 5 matter.

### CO-20 Identifiability analysis — run before any fitting

```python
def fisher_information(model, params, observables, excitation) -> NDArray
def identifiability_report(FIM) -> IdentReport   # rank, cond, aliased pairs
```

**Procedure.** Build the sensitivity matrix `∂y/∂θ` by complex-step or central
differences, form `FIM = JᵀWJ`, then report rank, condition number, and the
near-null-space directions (aliased parameter combinations).

**`CO-20a`** Transformer with top-oil temperature as the only observable: assert
`C_w, G_wo, k_H` are **not** jointly identifiable, and name the aliased combination.
**`CO-20b`** Doc A's 9-coefficient oil model against 12 samples/year: assert
`rank(FIM) ≪ 9`. **This is the quantitative justification for §2.6's exclusion** —
the honest basis for a scope cut, rather than an opinion.

### CO-21 SALib screening

Morris screening first (cheap, ranks), then Sobol on the survivors.

```
pixi run asset-sens --asset transformer --method morris --trajectories 200
```

**Output:** ranked parameter list with elementary-effect means, feeding directly
into the field-data request. **`CO-21a`** Ranking is stable under a seed change.
**`CO-21b`** The top-5 list is written to `docs/data_request.md` as the measurement
priority — closing the loop from sensitivity to data acquisition.

### CO-22 Parameter fitting

`scipy.optimize.least_squares` (TRF) with bounds from each parameter's `Range`.
**Fit-only / test-only data split is enforced by whole events or whole days, never
by adjacent samples** (Doc A is right about this and it is easy to get wrong).

**`CO-22a`** Synthetic round-trip: generate with known `θ*`, add noise, recover
within the identifiable subspace.
**`CO-22b`** Fitting promotes `evidence` `PLACEHOLDER → ASSET_CALIBRATED` only
when the held-out check passes its pre-declared acceptance rule.

### WP7 gate

- [ ] `CO-20a/b`, `CO-21a/b`, `CO-22a/b` green
- [ ] Per-asset identifiability report committed
- [ ] `docs/data_request.md` generated from screening, not hand-written

---

## WP8 — Study runner and differential metrics

**Goal.** Produce the one claim v0.1 can defend: **A causes N× the exposure of B.**

### ST-01 Case specification and paired execution

```python
@dataclass(frozen=True)
class Case:
    case_id: str; asset: str; params_id: str
    excitation: ExcitationSpec; state0_id: str | None
    t_end_s: float; seed: int        # derived from case_id, never worker index
```

**`ST-01a`** Results are independent of worker count and completion order.
**`ST-01b`** The same `case_id` gives the same seed on every machine.
**`ST-01c`** Paired cases **share** parameter draws (common random numbers).

### ST-02 Differential metrics — the core output

Absolute damage carries the full uncertainty of unknown prefactors. The **ratio**
between two policies under shared parameters cancels most of it.

**Demonstration `ST-02a`** — shared log-normal prefactor uncertainty (`σ_lnK = 0.8`),
true effect `ln(D_A/D_B) = 0.15`, n = 20000 paired draws:

| Quantity | Value |
|---|---|
| `sd(ln D_A)` (absolute) | `0.807252` |
| `sd(ln D_A − ln D_B)` (paired) | `0.070853` |
| **variance reduction** | **`129.8×`** |
| recovered mean log-ratio | `0.150939` (truth `0.15`) |

A 15 % effect is **invisible** against `sd = 0.81` absolute but **clean** at
`sd = 0.071` paired. This is why differential metrics are the primary output and
absolute life is tier-gated — and it is reproducible with
`seed = 20261019`.

**`ST-02b`** Report the ratio with a bootstrap CI. If the CI spans 1.0, the result
is **"cannot distinguish"**, not a ranking.
**`ST-02c`** The numerical/filter spread from `GT-05` and `TR-05f` is carried into
the comparison; a claimed effect smaller than that spread is reported as a tie.

### ST-03 Result schema

Parquet, one summary row per case + optional time series.
**Mandatory columns:** `case_id`, `asset`, `impl_tier`, `evidence_tier`,
`params_id`, `params_hash`, `seed`, `git_commit`, `pixi_lock_hash`, `wall_time_s`,
`peak_memory_MB`, `status`, `flags`, then exposure/peak/damage columns.

**`ST-03a`** A failed case appears with `status="failed"` and **null** damage —
never `0.0` (Doc A SW-08).
**`ST-03b`** A case is reproducible from its row plus the repo at `git_commit`.

### ST-04 Execution

Serial first, then `concurrent.futures.ProcessPoolExecutor`.
**No MPI in v0.1.** `N_worker × N_thread ≤ N_core`; set
`OMP_NUM_THREADS=1` per worker before pool creation.

### WP8 gate

- [ ] `ST-01a–c`, `ST-02a–c`, `ST-03a/b` green
- [ ] 1 vs 4 workers → identical results within declared tolerance
- [ ] A study artefact directory reproduces from its manifest alone

---

## WP9 — State estimation and evidence-gated reporting

**Goal.** Earn the word "twin". Doc A defines a digital twin as receiving
measurements, then specifies a tool with no state estimator. One UKF per asset is
achievable, open-source, and is the most convincing single addition.

### SE-01 Per-asset UKF

`filterpy` (MIT). Start with the transformer: 2 states (`T_w`, `T_o`), 1 observable
(`T_o`, top-oil, routinely measured).

```python
ukf = UnscentedKalmanFilter(dim_x=2, dim_z=1, fx=transformer_fx, hx=lambda x: x[1:2], ...)
```

**`SE-01a`** Synthetic recovery: inject a known `T_w` trajectory, observe noisy
`T_o` only, recover `T_w` within 2σ at ≥ 95 % of steps.
**`SE-01b`** Innovation whiteness: the normalised innovation sequence passes a
Ljung–Box test — the standard check that the filter is consistent rather than
merely converging.
**`SE-01c`** A sensor dropout widens the covariance; it must not silently freeze
the estimate.
**`SE-01d`** Aging accumulators are **never** reset by a filter update; only an
explicit maintenance event may change them (`CF-15`).

### SE-02 Evidence-gated report generation

```python
def render_report(results, claim: ClaimLevel) -> Report   # raises InsufficientEvidence
```

Doc A's claim ladder becomes code:

| Claim | Minimum evidence |
|---|---|
| exposure accumulated | `LITERATURE` |
| damage **ratio** between cases | `LITERATURE` |
| absolute damage fraction | `ASSET_CALIBRATED` |
| remaining life | `FIELD_VALIDATED` (unreachable in v0.1 — by design) |

**`SE-02a`** Requesting remaining life at any v0.1 tier raises `InsufficientEvidence`.
**`SE-02b`** Every reported figure carries its tier in the output.

### WP9 gate

- [ ] `SE-01a–d`, `SE-02a/b` green
- [ ] UKF runs on the transformer fixture faster than real time
- [ ] No report path can emit a remaining-life number in v0.1

---

## 6. What is derivable from open literature

Direct answer to "what specifications can be derived from open literature",
and the honest shape of the open-source story.

### 6.1 Available openly

| Item | Open source of record | WP |
|---|---|---|
| Transformer thermal structure, time-constant ranges | IEC 60076-7, IEEE C57.91 (public equations; **cite clause**) | WP2 |
| Typical oil/winding rises for ONAN/ONAF | IEC 60076-2 | WP2 |
| Paper aging `B`, `T_ref` | IEEE C57.91 | WP2 |
| Harmonic loss factors `F_E`, `F_S` | IEEE C57.110 | WP2 |
| Rainflow algorithm | ASTM E1049-85 | WP1 |
| Haibach slope `2m−1`, steel knee `1e6–1e7` | fatigue handbooks; NIMS datasheets | WP1/4 |
| **Steel S-N curves (free)** | **NIMS Fatigue Data Sheets** | WP4 |
| **IGBT lifetime coefficients** | **LESIT; CIPS08 (Bayerer et al.)** | WP5 |
| Film capacitor Arrhenius + voltage exponent | manufacturer application notes | WP5 |
| **Li-ion cell aging datasets** | **Severson (Nature Energy 2019); CALCE; NASA Prognostics** | WP3 |
| **Fitted cell aging models** | **BLAST-Lite (NREL, BSD-3)** | WP3 |
| Cell parameter sets | PyBaMM `Chen2020`, `OKane2022` | WP3/7 |
| Fluid properties | CoolProp (MIT) | future cooling |
| Sensitivity methods | SALib (MIT) | WP7 |
| UKF | filterpy (MIT) | WP9 |

### 6.2 Not available openly — the real constraint

**The free part is the cheap part.** Every *solver* in this project is free. Not one
*life curve for your actual asset* is:

| Gap | Consequence | Mitigation in v0.1 |
|---|---|---|
| This shaft's material S-N + geometry + SCF | Absolute shaft life unobtainable | `FIXTURE` + exposure/ratios only (`GT-05`) |
| This IGBT module's `ΔT_j`–`N_f` | Absolute converter life unobtainable | LESIT/CIPS08 as *literature*, tier `LITERATURE` |
| This cell's calibrated aging | Absolute battery life unobtainable | BLAST-Lite + open datasets, ratios only |
| Transformer heat-run test report | `k_H`, `C_w` unidentifiable | `CO-20a` names the aliasing |
| Paywalled standard text | Cannot vendor | implement public equations, cite clause |

**Therefore v0.1's defensible claim is the ratio, not the number.** That claim needs
zero OEM data, and `ST-02a` shows it is ~130× better conditioned than the absolute
claim. This is the strategic core of the specification.

### 6.3 Licence posture

Core stack is permissive: NumPy/SciPy/pandas (BSD), SALib/filterpy/rainflow (MIT),
PyBaMM (BSD-3), BLAST-Lite (BSD-3), pyarrow (Apache-2.0). pyLife (Apache-2.0) is
PyPI-only and optional — see `CO-06`.
**ANDES is copyleft (GPL-3 — verify)**; it is not required for WP2–WP5, so keep it
behind a pixi feature or drop it. **Modelica/Dymola is off the critical path** — it
breaks both the laptop promise and the open-compiler promise (Doc A's own
AutoCSM/Dymola note). Resolve all of this in `docs/licenses.md` at the WP0 gate.

---

## 7. Deployment stages

| Stage | Artefact | Gate |
|---|---|---|
| 1 Pinned env | `pixi.lock`, `docs/licenses.md` | clean checkout reproduces a golden case |
| 2 Study artefact | manifest + parquet + report + env hash, one directory | reproducible from manifest alone |
| 3 Laptop batch | serial → `ProcessPoolExecutor` | measured speedup, bounded memory, visible failures |
| 4 Historical replay | per-asset fit + held-out check | WP7 acceptance rules pass |
| 5 Live shadow | read-only feed, predictions logged | security review; no actions |
| 6 Advisory | human in the loop | Doc A ch. 10 field evidence |

Closed-loop control is **permanently out of scope** for this tool.

---

## 8. Open questions for the author

1. **Transformer population** — oil-filled or dry-type? Doc A's entire oil/paper
   chapter assumes oil-filled; many data-centre distribution units are cast-resin.
   This changes WP2's aging model, not just its parameters.
2. **Tap changer** — worth adding as `TR-06`? Operation counts are *countable*
   without OEM data, LTC wear is a leading maintenance driver, and it is driven by
   exactly the voltage volatility of interest. Highest evidence-per-effort of
   anything not currently in Doc A.
3. **Cooling plant** — chillers/CDUs/pumps have start-stop cycling wear from load
   volatility. Fifth asset in v0.2, or permanently a boundary condition?
4. **Which asset is the demonstrator** for the first external result? Spec assumes
   transformer (best evidence); WP4 is furthest along in code but weakest in evidence.
5. **Fixture realism** — should WP2's 2.5 MVA fixture be replaced by a real
   nameplate before WP6, so conformance runs against a plausible unit?

---

## Appendix A — Golden vector index

| ID | Asset | Quantity | Verified value |
|---|---|---|---|
| `CO-11a` | kernel | rainflow total count | `4.0`, residue 7 points |
| `CO-12a` | kernel | Basquin vs Haibach at `0.5 S_k` | `128.0×` |
| `TR-03a` | transformer | `F_E` / `F_S` (6-pulse) | `4.44583256` / `1.24111350` |
| `TR-04c` | transformer | 2 h @ 120 °C | `5.413679 h` |
| `TR-04e` | transformer | `⟨F⟩/F(⟨T⟩)`, ±15 K | `2.2868` |
| `TR-05a` | transformer | steady `T_H` at rated | `101.0000 °C` |
| `BT-01a` | battery | `I_cell` at 500 kW | `65.87277758 A` |
| `BT-02a` | battery | `v_p` at `h = R_pC_p` | `0.0166626979 V` |
| `BT-03a` | battery | `Δz`, 50 A × 600 s | `0.08333333` |
| `GT-02a` | turbine | `W_t` | `6.5449846950e-04 m³` |
| `GT-03a` | turbine | Goodman `S_a,eq` | `145.454545 MPa` |
| `GT-04c` | turbine | Haibach vs Basquin | `3.051758e-03` vs `3.906250e-01` |
| `CV-01a` | converter | `P_cond` | `172.8000 W` |
| `CV-03a` | converter | `Z_th(1 s)` | `0.1332332358 K/W` |
| `CV-05a` | converter | `E_c` from 10 K halving | `75984.5405 J/mol` |
| `ST-02a` | study | paired variance reduction | `129.8×` |

All values computed with `numpy 2.x / scipy 1.18` in the project pixi environment
and reproducible via `tools/spec/compute_golden.py` (WP0 deliverable).

**Fixture values are not asset data.** Every fixture-derived number above tests
*code correctness*, never physical accuracy.

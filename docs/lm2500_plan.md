# LM2500 Gas Turbine + Generator Model — Plan & Implementation Notes

Running document tracking design decisions, implementation progress, and open questions.

---

## Reference Document

**Source:** `MSC01A_1211_B5_LM2500-Gas-Turbine_-r5b_rg.pdf` (Military Sealift Command LM2500 Combustion Turbine Course, Technical Training Professionals, 2021)

---

## Phase 1 — LM2500 Turbine Model (Steady-State Surrogate)

### Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Variant | LM2500 base (22 MW shaft) | Per user selection; matches training manual specs |
| Surrogate basis | Scale existing ThermoPower surrogate | Fastest path; ThermoPower shape is reasonable for an aero-derivative at this fidelity |
| Generator rating | 23.0 MW electrical | From PDF: "3-phase brushless, water-cooled unit, rated at 23.0 MW" |
| Frequency | 60 Hz (3,600 rpm power turbine) | Per PDF and user selection |

### Key Specifications (from PDF)

| Parameter | Value | Notes |
|-----------|-------|-------|
| Shaft power | 22 MW | Base LM2500 |
| Generator electrical rating | 23.0 MW | 3-phase, brushless, water-cooled |
| Heat rate | 8,746 BTU/kW-hr | Electrical basis |
| Heat rate (shaft) | 6,522 BTU/shp-hr | Shaft-horsepower basis |
| Thermal efficiency | ~39.0% | = 3,412 / 8,746 |
| Exhaust temperature | 965°F / 518°C / 791 K | At full load |
| Configuration | Two-shaft, free power turbine | HP rotor + LP/power turbine independent |
| HP rotor speed (idle) | ~5,800 rpm | |
| HP rotor speed (base load) | ~10,000 rpm | Trip at 9,950 rpm |
| Power turbine speed | 3,600 rpm (constant) | Direct-coupled to generator (2-pole) |
| Compressor | 16-stage axial | Discharge ~270 psig |
| HP turbine | 2-stage | Drives compressor |
| Power turbine | 6-stage | Drives generator |
| Combustor | Single annular (SAC) | 30 fuel nozzles, 2 igniters |
| Fuel | Natural gas (primary) | SAC model also supports fuel oil, LNG, etc. |

### Surrogate Scaling Approach

The existing ThermoPower-derived surrogate (`gas_plant/unit.py`) already supports `rated_power_mw` scaling:
- Power, fuel flow, exhaust mass flow scale linearly with rated MW
- Exhaust temperature is treated as size-invariant (thermodynamic property)
- Efficiency is computed from power/(fuel × LHV) at evaluation time

For the LM2500 base model:
```python
plant = GasTurbinePlant(rated_power_mw=22.0)
```

**Limitation:** The ThermoPower surrogate's part-load shape (exhaust temp profile, efficiency curve) was calibrated to a 235 MW heavy-duty frame. The LM2500 may behave differently at part load — in particular:
- Exhaust temperature likely rises more steeply at part load (aero-derivative characteristic)
- Part-load efficiency may be flatter due to variable geometry (IGVs + 6 stages variable stators)

**Accepted for now:** The scaled surrogate is a first approximation. A future refinement could introduce an LM2500-specific part-load table if public performance data is sourced.

### Adjustments to Match LM2500

| Parameter | ThermoPower default | LM2500 override | Source |
|-----------|-------------------|-----------------|--------|
| `rated_power_mw` | 235.0 | 22.0 | PDF: 22 MW shaft → ~22 MW at generator terminals (losses ≈ 1 MW absorbed into 23 MW rating) |
| `fuel_lhv_j_kg` | 49e6 | 49e6 | Same (natural gas) |
| `co2_per_fuel_kg` | 2.75 | 2.75 | Same (NG stoichiometry) |
| Full-load efficiency | ~40% (ThermoPower) | ~39% (8,746 BTU/kW-hr) | Close enough — within surrogate shape tolerance |
| Exhaust temp (full load) | 843 K (ThermoPower) | 791 K (PDF: 965°F) | **Difference** — LM2500 is 52 K cooler at full load |

The exhaust-temperature difference means the existing surrogate's exhaust-T column will overestimate by ~6% at full load. This matters if we later couple to a CCPP bottoming cycle but does NOT affect electrical dynamics in Phase 2.

---

## Phase 2 — Electrical Generator System (Transient Dynamics)

### Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Generator model | GENCLS (classical) | Simplest; adequate for frequency/torque studies |
| Inertia H | 2.5–3.5 s (TBD) | Aeroderivative is lighter than heavy-duty; literature range |
| Governor model | TGOV1 | Already in repo; droop + lag suitable for frequency response |
| Governor speed | Fast (T1=0.2, T3=2.0 s) | Aero-derivative governors are faster than heavy-duty |
| Network | Single-bus islanded | Data-center load on same bus as generator |
| Electrical system | 60 Hz, 3,600 rpm | Per PDF |

### Machine Parameters (Phase 2 targets)

| Parameter | Symbol | Planned Value | Notes |
|-----------|--------|--------------|-------|
| Inertia constant | H | 3.0 s | Mid-range for aero-derivative GT-gen set |
| Damping coefficient | D | 1.5 pu/pu | Moderate, accounts for load damping |
| Machine MVA rating | Sn | 23.0 MVA | = generator rated MW (unity PF) |
| Droop | R | 0.05 (5%) | Standard |
| Governor lag | T1 | 0.2 s | Fast fuel valve |
| Governor lead | T2 | 0.4 s | |
| Turbine time constant | T3 | 2.0 s | Faster than heavy-duty (~5 s) |
| Max valve position | VMAX | 1.1 | Conservative; LM2500 can't exceed rated for long |

### Two-Shaft Consideration

The LM2500 is a free-power-turbine design:
- HP rotor (compressor + HP turbine): 5,800–10,000 rpm, speed varies with load
- Power turbine + generator: 3,600 rpm constant (held by governor)

For the GENCLS representation, we model the **power-turbine + generator** inertia only (the mass that participates in frequency dynamics). The HP rotor is aerodynamically coupled but mechanically independent — its inertia does NOT directly contribute to electrical frequency response. This is the correct modeling choice for GENCLS.

---

## Phase 3 (Future) — Data-Center Load Profile & Transient Simulation

### Concept

- Derive a time-varying electrical demand from the MIT Supercloud dataset (or synthetic RAPS workload)
- Feed that demand as the imposed load on the islanded LM2500 + generator bus
- Run transient simulation; record speed, torque, frequency, fuel, CO2

### Open Items

- [ ] Which MIT Supercloud trace window to use
- [ ] How to convert CPU/GPU utilization → electrical MW (calibration)
- [ ] Load profile resampling strategy for ANDES timestep
- [ ] Reserve margin policy (how close to 22 MW peak is acceptable?)

---

## Implementation Log

| Date | Action | Status |
|------|--------|--------|
| 2026-06-11 | Created plan document | ✅ |
| 2026-06-11 | Created Phase 1 notebook (`notebooks/lm2500_model.ipynb`) | ✅ |
| 2026-06-11 | Phase 2: LM2500 generator + ANDES islanding transient | ✅ |
| | Phase 3: MIT Supercloud load profile integration | pending |

## Phase 2 Results

### Islanding Scenario (18 MW load, grid tie opens at t=2 s)
- Peak frequency excursion: **100 mHz** (60.10 Hz)
- Steady-state droop offset: **42.5 mHz** above nominal
- Settling time: ~10 s
- Speed range: 3,600 – 3,606 rpm
- Torque range: 46.3 – 47.8 kN·m

### Load Step Scenario (15 MW → 19 MW step at t=5 s during island)
- Peak frequency excursion: **87 mHz**
- Steady-state offset: **37 mHz**
- Governor settles within ~8 s
- No frequency violation (all within ±0.5 Hz data-center tolerance)

### Key Observations
- The aeroderivative-class parameters (H=2.8 s, fast governor T3=1.5 s)
  produce rapid frequency recovery despite lower inertia than heavy-duty frames
- The 4% droop and fast fuel valve response keep excursions small
- 18% reserve margin (22 MW rated vs 18 MW load) provides adequate headroom

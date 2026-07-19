# Constants Dossier — Provenance and Defensibility

Single source of truth for every physical constant and model parameter in
`gas_plant/`, `gas_plant/dynamics/`, and `gas_plant_andes/`. Maintained as part
of the V&V campaign (see `docs/vv_log.md`).

**Status legend**

| Status | Meaning |
|---|---|
| **PINNED** | Traceable to a specific document (report table, datasheet, textbook section) archived in `papers/` or `refs/`, or to a standards-body typical value. Defensible as-is. |
| **ESTIMATED** | Derived from pinned data by a documented method, or a literature *range* with a chosen point value. Defensible with the derivation shown; sensitivity should be quoted. |
| **PLACEHOLDER** | Invented or generic value filling a gap. Must not be presented as machine-specific. Replace with vendor data before design-grade use. |

---

## 1. GGOV1 governor (`gas_plant/dynamics/ggov1.py`)

Per-unit base: turbine MW (`Trate_mw`), per IEEE PES-TR1 (2013) §3.3.
The PES-TR1 Appendix C table is transcribed in `papers/pes-tr1-2013-appendixC.txt`
and was verified value-by-value against `GGOV1Params` defaults (V&V critique, 2026-07-18).

| Parameter | Value | Status | Source / justification |
|---|---|---|---|
| R (droop) | 0.04 | **PINNED** | PES-TR1 App. C typical; consistent with aero islanded practice (LM2500 Pocket Guide §1.3.17) |
| rselect | 1 | PINNED | PES-TR1 App. C. NOTE: for a *single islanded* unit, isochronous (rselect=0) is operationally standard; both modes implemented and verified. Open user decision D1. |
| Tpelec | 1.0 s | **PINNED** | PES-TR1 App. C |
| MaxERR/MinERR | ±0.05 | **PINNED** | PES-TR1 App. C |
| Kpgov / Kigov | 10 / 2 | **PINNED** (generic) | PES-TR1 App. C. Hannett & Khan (1993) Table 1 shows real Alaskan units had Rowen `w` = 26–45 (≫ the "typical" 25); LM2500 islanded service likely needs Kpgov 20–30. **Flagged: retune against vendor step data when available.** |
| aset | 0.01 pu/s | **PINNED** | PES-TR1 App. C (GE GT) |
| Ka / Ta | 10 / 0.1 s | **PINNED** | PES-TR1 App. C |
| Ldref | 1.0 (turbine base) | **PINNED** | PES-TR1 App. C; enforces the 22 MW continuous rating via the temperature limiter (verified by test) |
| Kpload / Kiload | 2 / 0.67 | **PINNED** | PES-TR1 App. C |
| Tfload / Tsa / Tsb | 3 / 4 / 5 s | **PINNED** | PES-TR1 App. C |
| Tact | 0.5 s (default), **0.15 s LM2500** | ESTIMATED | PES-TR1 default is for heavy-duty hydraulic actuators; LM2500 uses a Woodward MkVIe/NetCon-class electronic valve — faster. 0.15 s is an engineering estimate, no vendor time constant in hand. |
| Vmax / Vmin | 1.0 / 0.15 (stroke) | **PINNED** | PES-TR1 App. C. Vmin < Wfnl is intentional: below no-load fuel the net shaft power is negative (compressor drag). |
| Ropen / Rclose | ±0.10 pu/s | **PINNED** (generic) | PES-TR1 App. C. Real LM2500 accel/decel schedules are NGG- and ambient-dependent (MMO-010, not in hand). |
| Kturb | 1.5 | **PINNED** | PES-TR1 App. C |
| Wfnl | 0.2 | **PINNED** | PES-TR1 App. C |
| Tb / Tc | 0.1 / 0 s | **PINNED** | PES-TR1 App. C |
| Teng, Kdgov, db, KIMW, Rup/Rdown | inactive | PINNED | PES-TR1 App. C defaults (0 / disabled); **not implemented** — documented subset |
| flag | 1 (Wf ∝ speed) | ESTIMATED | PES-TR1 default. LM2500 has an electronically controlled fuel-metering valve; flag=0 may be more appropriate. Sensitivity is small near ω=1. |
| Dm | 0 | **PINNED** | PES-TR1 App. C. Semantics per PSS/E: >0 damping, <0 fuel-speed exponent (both implemented, sign verified by test). |
| Kbc_speed/accel/temp | 100/100/50 | PLACEHOLDER (implementation knob) | Back-calculation anti-windup gain — NOT a physical parameter. Verified: 25× range changes a step nadir by 2.1 mHz (test-gated at 5 mHz). |

## 2. Machine / swing equation

| Parameter | Value | Status | Source / justification |
|---|---|---|---|
| Sn | 23 MVA | ESTIMATED | Site value used throughout the study; consistent with a 22 MW machine at PF ≈ 0.95. No generator nameplate archived. **Action: archive nameplate.** |
| Trate | 22 MW | ESTIMATED | LM2500 base gen-set rating (ISO). Pocket Guide Table 1-2 quotes 26,250 BHP ≈ 19.6 MW *shaft* for the Navy propulsion variant; industrial gen-set variants are rated 21–23 MW ISO. The 22 MW figure is the site convention. **Tension documented; archive the actual gen-set datasheet.** |
| H | 2.8 s | **PLACEHOLDER** | Unsourced (inherited from the original notebook). Aero gen-sets are light: literature range ≈ 1.5–3.5 s on machine base. Frequency nadir scales ≈ 1/H. **Highest-priority constant to pin (vendor WR² or inertia test). Quote nadir sensitivity over H ∈ [1.5, 3.5] in any publication.** |
| D | 0 | **PINNED** (decision) | V&V fix G5: load-frequency sensitivity is carried entirely by α below; a machine D on top double-counts (GENCLS has no damper-winding model to justify it). |
| α (load damping) | 1.5 | **PINNED** (range) | Kundur (1994) §11.1.4: typical 1–2 %ΔP per %Δf. Point value within range; data-center loads (PSU-dominated) may be lower — sensitivity worth quoting. |
| ω₀ | 2π·60 rad/s | PINNED | 60 Hz system |
| Poles | 2 (3600 rpm) | PINNED | LM2500 PT drives a 2-pole machine at 3600 rpm / 60 Hz (Pocket Guide: NPT 3600 rpm) |

## 3. Fuel calibration (V&V fix G2)

| Parameter | Value | Status | Source / justification |
|---|---|---|---|
| eta_design | 0.365 | ESTIMATED | GE LM2500 gen-set ISO efficiency is ≈ 35–38 % depending on variant/coupling (PGT25 class ≈ 37 %; older base LM2500 ≈ 35–36 %). Midpoint chosen. Cross-check: NAVEDTRA/MSC01A full-load fuel 9,000 lb/h = 1.134 kg/s at 26,250 BHP = 19.6 MW shaft → 35.3 % *shaft* efficiency — consistent. **Archive the gen-set heat-rate sheet to upgrade to PINNED.** |
| LHV | 49 MJ/kg | **PINNED** (range) | Pipeline natural gas LHV 47–50 MJ/kg (GPSA / EIA); CH₄ is 50.0. |
| wf_base_kg_s | 1.419 (derived) | ESTIMATED | = [Trate/(eta_design·LHV)] / (Ldref/Kturb + Wfnl); pure derivation from the above. |

## 4. Two-mass gas path (Tier C, `multishaft.py`)

| Parameter | Value | Status | Source / justification |
|---|---|---|---|
| ω_hp_idle | 0.516 | **PINNED** | 4,900 rpm idle / 9,500 rpm full NGG — LM2500 Pocket Guide Table 1-2 |
| NGG_full | 9,500 rpm | **PINNED** | Pocket Guide Table 1-2 |
| H_pt / H_hp | 2.5 / 0.3 s | **PLACEHOLDER** | Split of the (itself unpinned) total H=2.8 using the qualitative rule "HP rotor ≈ 20–30 % of system kinetic energy". No vendor inertia data. |
| K_couple | 1/(1−0.516) = 2.066 | **PLACEHOLDER** | Normalization of an *invented linear* gas-path law P = K(ω_hp − ω_idle). Real FPT power vs NGG speed is strongly nonlinear (~cubic) and PT-speed dependent. Implied spool time constant M_hp/K ≈ 0.29 s vs real NGG spool response 0.5–1.5 s. **Tier C transient claims are qualitative until calibrated against a spool-up trace.** |
| D_hp | 0 | PINNED (decision) | A D·(ω−1) term referenced to 60 Hz is wrong for a rotor whose natural speed is the gas-path operating point. |

## 5. Torsional / fatigue (Tier E, `torsional.py`)

| Parameter | Value | Status | Source / justification |
|---|---|---|---|
| H_pt_only / H_gen | 0.5 / 2.0 s | **PLACEHOLDER** | Split of Tier C's 2.5 s using "free turbine ≈ 20–30 % of PT+gen inertia" |
| f_torsion | 22 Hz | **PLACEHOLDER** | "Typical aero gen-set 18–30 Hz" — no vendor torsional analysis in hand. Stiffness k is back-solved from this number, so all cycle counts inherit it. |
| ζ (modal damping) | 0.01 | **PLACEHOLDER** | Typical structural damping ratio |
| Shaft OD/ID | 150 / 50 mm | **PLACEHOLDER** | "Typical 22 MW-class coupling shaft" — no drawing |
| Steel / UTS / yield | AISI 4340, 1000 / 800 MPa | ESTIMATED | Standard handbook values for 4340 Q&T; the *material choice itself* is a placeholder |
| Basquin m, N_ref, Sa_ref | 9, 1e8, 0.3·UTS | ESTIMATED | High-strength-steel torsional S-N conventions (m 9–12; endurance ≈ 0.3 UTS shear). Adequate for *relative* duty-cycle comparisons only. |
| Su_shear | 0.6·UTS | **PINNED** (convention) | Standard distortion-energy approximation |

**Blanket caveat (already in code):** Tier E output is duty-cycle *screening*,
not a design-grade life assessment, until shaft drawing + material certs +
verified S-N data replace the placeholders.

## 6. Gas plant surrogates (`gas_plant/`)

| Parameter | Value | Status | Source / justification |
|---|---|---|---|
| ThermoPower surrogate table | `data/gas_turbine_surrogate.csv` | **PINNED** (to the model) | Offline sweep of ThermoPower `GasTurbineSimplified` (235 MW). Pinned to the *Modelica model*, not to any real machine. Full-load η = 39.6 %, T_exh 843 K, m_exh 614 kg/s — F-class-plausible. **Valid scaling range: treat ±30 % of 235 MW as trustworthy; the LM2500 (22 MW) use is a 10.7× extrapolation across engine classes — superseded by the native GGOV1 fuel path (fix G2).** |
| LHV | 49 MJ/kg | PINNED (range) | as §3 |
| CO₂/fuel (generic) | 2.75 kg/kg | **PINNED** | Stoichiometric CH₄: 44/16 = 2.75 |
| CO₂/fuel (LM9000) | 2.65 kg/kg | **PINNED** (derivation) | Back-calculated from datasheet 492.9 kg CO₂/MWh at 56.723 MW / 39.52 % (derivation in code); EPA natural-gas factor ≈ 2.63–2.69 kg/kg — consistent |
| LM9000 design point | 56.723 MW / 39.52 % / 729 K | ESTIMATED | Quoted from a GE LM9000 datasheet that is **not archived in the repo**; GE public materials also show ~66–75 MW variants. **Action: archive the exact datasheet PDF in refs/ or the numbers stay ESTIMATED.** |
| AFR (LM9000) | 50 | ESTIMATED | LM2500 measured AFR 49.2 (NAVEDTRA air 442,800 lb/h / fuel 9,000 lb/h, via MSC01A); LM9000 DLE assumed same lean regime. Gives 149 kg/s exhaust vs published ≈ 158 kg/s (−6 %). Fixed-AFR exhaust flow is wrong at part load (real machines hold airflow flatter); documented limitation. |
| no_load_fuel_frac (Willans) | 0.2 | ESTIMATED | Consistency choice with GGOV1 Wfnl = 0.2 (PES-TR1). Willans line is the standard affine fuel-vs-load form. |
| eta_bottoming (heavy CCPP) | 0.32 | ESTIMATED | Typical 3-pressure-reheat HRSG bottoming efficiency; produces 57.1 % CC at full load (modern F-class 1×1: 57–59 %) |
| eta_bottoming (LM9000 CC) | 0.288 (auto-tuned) | ESTIMATED | Solved so GT+ST = 72.471 MW datasheet CC rating |
| T_stack | 363 K (heavy) / 380 K (LM9000) | ESTIMATED | Heavy value matches ThermoPower CCPP_Sim3 sink (362.3 K); LM9000 value generic |
| cp (flue gas) | 1100 (heavy) / 1050 (LM9000) J/kg/K | **PLACEHOLDER** | Two different "standard values" in sibling modules; flue-gas cp at HRSG mean temperature ≈ 1080–1150. **Action: unify on one cited value.** |
| Linear ΔT bottoming derate | — | ESTIMATED (form) | Pragmatic fit form. Former claim of PES-TR1/CIGRE-238 provenance retracted (fix P4). |

## 7. ANDES coupling defaults (`gas_plant_andes/defaults.py`)

| Parameter | Value | Status | Source / justification |
|---|---|---|---|
| H (heavy GT) | 5.0 s | **PINNED** (range) | Kundur Table; heavy-duty GT gen-sets 4–6 s machine base |
| D | 2.0 | ESTIMATED | Legacy GENCLS lumped value; inconsistent with the G5 decision in the scipy path — acceptable only while the ANDES case models load as constant-P without explicit damping. Flagged for Phase 3 v2 case. |
| TGOV1 R/T1/T2/T3 | 0.05/0.5/1.0/5.0 | ESTIMATED | Generic heavy-duty values (Kundur-style); not machine-specific |
| TGOV1 VMAX | 1.2 | **PLACEHOLDER** | Allows 20 % steady overload; contradicts the thermal-cap philosophy. Phase 3 v2 case sets 1.0 on turbine base. |
| EXST1 TR/KA/TA | 0.01/200/0.02 | **PINNED** (typical) | IEEE 421.5 ST1-type typicals; currently dead code (GENCLS takes no exciter) — activated in Phase 3 v2 (GENROU) |
| Sn = rated MW | — | **PLACEHOLDER** | PF=1.0 assumption; Phase 3 v2 uses Sn = MW/0.85 |
| Tie x = 0.05 pu | — | ESTIMATED | "~10 km of 230 kV" order-of-magnitude |

## 8. Priority actions to harden the dossier

1. **H (LM2500 gen-set)** — obtain WR²/GD² from vendor documentation; until
   then, publish nadir sensitivity over H ∈ [1.5, 3.5] s.
2. **LM9000 datasheet** — archive the exact PDF the 56.723 MW / 39.52 % /
   492.9 kg/MWh numbers came from.
3. **LM2500 gen-set heat-rate sheet** — upgrades eta_design (0.365) to PINNED.
4. **Kpgov/Kigov retune** — against vendor step-response data or an MMO-010
   transcription (Hannett Table 1 suggests real units are 2–4× hotter).
5. **Spool-up trace** — calibrate K_couple/M_hp (Tier C) against a recorded
   NGG acceleration; until then Tier C transient deltas are qualitative.
6. **Torsional package** — shaft drawing, material certs, S-N data before any
   absolute fatigue-life claim.

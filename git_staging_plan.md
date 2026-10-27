# Git staging plan — clean history, standalone components preserved

_Updated 2026-07-10 to cover LM9000, the LM2500 dynamics tiers (A/B/C/E), notebooks, reference papers, workload data, and the vendored RAPS repo. Original 7-commit ThermoPower/CCPP/ANDES sequence still lands first — the new work is appended as later commits so `git bisect` remains meaningful._

## 1. Repo layout decision

Keep this as a **single repo with logical subtrees**, not split repos. Reasons:
- `gas_plant_andes/` genuinely depends on `gas_plant/`, so a split forces awkward version pinning for a 2-person tool.
- The "standalone" requirement is satisfied by **package boundaries** (no import edges from `gas_plant` → `gas_plant_andes`, no shared deps in `pyproject.toml`), not by repo boundaries.
- If `gas_plant` later needs to ship independently (PyPI, sister project), `git subtree split --prefix=gas_plant` extracts it with full history. Don't pre-split before that's a real need.

Target top-level layout after all commits below have landed:

```
DataCenter/
├── gas_plant/                          # runtime, numpy/pandas/scipy only
│   ├── data/gas_turbine_surrogate.csv        # bundled (tracked)
│   ├── unit.py                               # generic GasTurbinePlant
│   ├── combined_cycle.py                     # analytical CCPP layer
│   ├── fleet.py                              # multi-unit wrapper
│   ├── lm9000.py                             # LM9000 first-principles model
│   └── dynamics/                             # ODE prime-mover + governor models
│       ├── tgov1.py                          # Tier A
│       ├── ggov1.py                          # Tier B
│       ├── multishaft.py                     # Tier C
│       └── torsional.py                      # Tier E
├── gas_plant_andes/                    # adds andes dep, depends on gas_plant
├── tools/
│   ├── build_surrogate/                # offline ThermoPower pipeline
│   │   └── libs/                             # vendored MSL 3.2.3  (see §4)
│   └── workload/                       # MIT Supercloud downloader/preprocessor
│       ├── mitsupercloud_download.py         # renamed from mitsupercloud_power.py
│       └── mitsupercloud_notes.md            # renamed from mitsupercloud_energy.txt
├── ThermoPower/                        # vendored library (see §4)
├── RAPS/                               # vendored ExaDigiT/RAPS (see §4)
├── notebooks/                          # LM2500 baseline + tier A/B/C/E + LM9000
├── examples/                           # ANDES islanding / resiliency
│   └── output/                               # regenerable — gitignored
├── tests/
├── data/                               # build artifacts + reference workload traces
│   └── gas_turbine_surrogate.csv             # raw sweep output (small, tracked)
├── docs/
│   ├── phase1_setup.md … phase6_setup.md     # moved from root
│   ├── tier_plan.md                          # LM2500 dynamics tier plan (already here)
│   ├── lm2500_plan.md                        # moved from root
│   ├── lm9000_implementation_summary.md      # moved from root, renamed lowercase
│   ├── electrical_transient_modeling.md      # moved from root
│   ├── andes_coupling_benefits.md            # moved from root
│   ├── gt_dynamics_notes.md                  # moved from root (was gTdynamics.txt)
│   └── figures/                              # any reference plots worth versioning
├── papers/                             # reference PDFs + digitized figures
├── refs/                               # equipment reference PDFs (see §5)
│   ├── lm2500/
│   └── lm9000/
├── environment.yml
├── pyproject.toml
├── README.md
└── .gitignore
```

File-move actions to run before the first commit (§9 has the exact sequence):
- `phase1_setup.md`, `phase2_3_setup.md`, `phase4_5_setup.md`, `phase6_setup.md` → `docs/`
- `electrical_transient_modeling.md`, `andes_coupling_benefits.md` → `docs/`
- `lm2500_plan.md` → `docs/lm2500_plan.md`
- `LM9000_IMPLEMENTATION_SUMMARY.md` → `docs/lm9000_implementation_summary.md`
- `gTdynamics.txt` → `docs/gt_dynamics_notes.md` (rename to Markdown; content is prose)
- `mitsupercloud_power.py` → `tools/workload/mitsupercloud_download.py`
- `mitsupercloud_pseudoCode.py` → delete (superseded by the working script) OR move to `tools/workload/archive/` if you want the pseudocode preserved
- `mitsupercloud_energy.txt` → `tools/workload/mitsupercloud_notes.md`
- `Load17` → `data/load17.csv` (it's a two-column time/load series — give it a real extension). Confirm what it is before renaming; if it's a canonical trace referenced by notebooks, update those references in the same commit.
- `ag-output.md` → **delete**. It's a transcript of a chat session about token estimation; not a project artifact.
- `mitsupercloud_energy.txt~`, `mitsupercloud_pseudoCode.py~` → delete (editor backups)
- Move the `.pdf` reference materials into `refs/` (see §5).

## 2. Commit sequence

Order matters: each commit should leave the tree buildable/importable for what existed at that phase. That makes `git bisect` and review meaningful.

The original 7 commits ship first (0–6), then the newer work as commits 7–11. Notebooks (commit 10) and workload data (commit 11) come last because they're heavy and shouldn't clutter earlier bisects.

| # | Commit | What's in it |
|---|---|---|
| 0 | `chore: repo scaffolding` | `.gitignore`, `environment.yml`, `pyproject.toml`, empty `gas_plant/__init__.py`, `README.md` stub, empty `docs/` |
| 1 | `phase 1: openmodelica via docker + vendored MSL` | `tools/build_surrogate/libs/` (MSL+ModelicaServices+Complex), `tools/build_surrogate/load_check.mos`, `docs/phase1_setup.md`, `ThermoPower/` (see §4) |
| 2 | `phase 2: gas-turbine parameter sweep → CSV surrogate` | `tools/build_surrogate/sweep_gt.mos`, `validate_gt.mos`, `validate_surrogate.py`, `data/gas_turbine_surrogate.csv` (raw build output), `docs/phase2_3_setup.md` (first half) |
| 3 | `phase 3: python GasTurbinePlant + Fleet` | `gas_plant/unit.py`, `gas_plant/fleet.py`, `gas_plant/data/gas_turbine_surrogate.csv` (bundled), `tests/smoke_test.py` (gas-only parts), `gas_plant/__init__.py` exports `GasTurbinePlant`/`Fleet` |
| 4 | `phase 4: analytical CCPP layer` | `gas_plant/combined_cycle.py`, `tools/build_surrogate/check_ccpp.mos`, `tools/build_surrogate/validate_ccpp.py`, CCPP test additions, `__init__.py` adds `CombinedCyclePlant`, `docs/phase4_5_setup.md` |
| 5 | `phase 5: packaging + smoke tests` | Final `pyproject.toml` package-data wiring, smoke test fillout, README polish |
| 6 | `phase 6: ANDES electrical-dynamics coupling` | `gas_plant_andes/` package, `examples/example_islanding.py`, `examples/example_resiliency.py`, `docs/electrical_transient_modeling.md`, `docs/andes_coupling_benefits.md`, `docs/phase6_setup.md`. **Do not** add `andes` to base `environment.yml` — keep as extras/optional install documented in `phase6_setup.md` §6.1. |
| 7 | `lm9000: first-principles two-spool model + docs` | `gas_plant/lm9000.py`, `gas_plant/__init__.py` re-export update, `docs/lm9000_implementation_summary.md`, `refs/lm9000/LM9000_Overview_042225.pdf`. If you have LM9000 tests, include them here. |
| 8 | `docs: lm2500 dynamics tier plan + references` | `docs/tier_plan.md`, `docs/lm2500_plan.md`, `docs/gt_dynamics_notes.md`, `papers/` (all 5 PDFs + digitized CSVs/PNGs), `refs/lm2500/` (both LM2500 PDFs + the PPTX if you want it versioned; see §5). No code changes. |
| 9 | `dynamics: tier A/B/C/E prime-mover + governor models` | `gas_plant/dynamics/` (`__init__.py`, `tgov1.py`, `ggov1.py`, `multishaft.py`, `torsional.py`). No notebook changes here — notebooks land in commit 10. |
| 10 | `notebooks: lm2500 baseline + tier A/B/C/E studies + lm9000` | All seven `notebooks/*.ipynb`. See §7 for the outputs-vs-nbstripout call. |
| 11 | `tools: workload downloader + reference trace` | `tools/workload/mitsupercloud_download.py`, `tools/workload/mitsupercloud_notes.md`, `data/load17.csv`. Do NOT commit the large workload artifacts (`data/dcgm.csv`, `data/scheduler_data.csv`, `data/nvidia_smi_first_1gb.parquet`) — see §6. |

Tag `v0.1.0` at commit 5, `v0.2.0` at commit 6, `v0.3.0` at commit 9 (LM9000 + dynamics tiers).

## 3. Branch strategy

For initial seeding: **commit all of the above directly to `main`** in one push. Don't bother with feature branches retroactively — the work is already done and you have no collaborators on it yet.

Going forward:
- `main` is always green (smoke tests pass, both examples run).
- Future work (resync, battery/PV, blackstart, LM2500 tier D if you add it, more turbine variants) goes on `feature/<name>` branches with PRs even if you're solo — it forces the per-phase doc discipline that's been working.

## 4. Handling vendored code (three cases now)

**`ThermoPower/`** (already a git checkout of the upstream repo):
- Recommended: **`git subtree add`** at commit 1, not `git submodule`. Subtrees check in the source; the repo stays self-contained and clonable without `--recurse-submodules`. Cost: ~5 MB and no free upstream updates. That's fine — ThermoPower 3.1 is the locked version, you've documented its CCPP_Sim3 bug, and chasing upstream is not in scope.
- Before running the subtree add, either `rm -rf ThermoPower/.git` and add it as normal tracked files, or split it out to a scratch branch first. Nested `.git` directories will confuse `git status` and later subtree operations.

**`tools/build_surrogate/libs/`** (vendored MSL ~17 MB):
- **Commit it.** Non-negotiable for reproducibility — `omc`'s SSL handshake fails against `libraries.openmodelica.org`, so anyone rebuilding the surrogate without this checked in will hit the same wall you did. Add `tools/build_surrogate/libs/README.md` noting the source (GitHub `modelica/ModelicaStandardLibrary` tag `v3.2.3+build.4`) so provenance is traceable. Section 11 now creates that README before commit 1.

**`RAPS/`** (~28 MB, has its own `.git`, upstream is ExaDigiT/RAPS):
- New since the original plan. Same call as ThermoPower: **`git subtree add`**, drop the inner `.git` first. RAPS is dependency-adjacent (workload/power simulator you're integrating against), and same "self-contained clone" argument applies.
- If you never actually run RAPS from this tree and only consulted its code as reference, consider **not committing it at all** and adding a `docs/references.md` line pointing to the upstream commit hash you read. Ask yourself: does any code in this repo import from `RAPS/` or shell out to `RAPS/main.py`? If not, don't vendor it.

If any of the vendored sizes feel heavy later, Git LFS is an option — but at ~50 MB combined, it's not worth the operational overhead yet. Skip LFS.

## 5. Reference PDFs and equipment manuals

Two piles, treat them differently:

**`papers/`** — academic references cited by the dynamics tier work (Rowen, Hannett, PES-TR1, Yee, CIGRE38). These are load-bearing: `docs/tier_plan.md` grounds Tier A/B/C/E parameters in specific tables and figures inside them. Commit them. Total ~7 MB. Also commit the digitized CSVs (`hannett_fig4_digitized.csv`, `hannett_figure5.csv`) and the extracted PNG figures (`hannett_fig4.png`, `hannett_fig5.png`) — they're the artifacts of the digitization work and belong next to the source paper. Delete the `.DS_Store` inside `papers/`.

**`refs/lm2500/`** and **`refs/lm9000/`** — equipment reference materials:
- `MSC01A_1211_B5_LM2500-Gas-Turbine_-r5b_rg.pdf` — **53 MB.** The training manual you calibrated the LM2500 generator rating and heat rate against. Two options:
  - **A (recommended if the repo will live on GitHub):** don't commit. Add `refs/lm2500/README.md` describing the document (Military Sealift Command LM2500 Course, 2021), the exact filename, and where you have it stored (e.g. shared drive path or a citation). GitHub's 100 MB single-file hard limit is not crossed, but repos with 50 MB blobs get flagged and clone times suffer.
  - **B:** commit it via Git LFS. Warrants standing up LFS just for this one file.
  - **C:** commit it raw. Simplest, and if this repo lives only on an internal server without bandwidth pressure it's fine.
  - Default to A. If unsure, do A now — you can always add it later.
- `lm2500-pocketguide.pdf` (~7 MB) — commit it under `refs/lm2500/`. Small enough that the "no big blobs" argument doesn't apply.
- `LM9000/LM9000 Overview 042225.pdf` (~770 KB) — commit as `refs/lm9000/LM9000_Overview_042225.pdf` in commit 7. Rename to drop the space.
- `Presentation/GT-LM2500.pptx` (~5.7 MB) — commit under `refs/lm2500/` if you consider the deck a project artifact; otherwise leave it out. It's a snapshot of talk content, not source-of-truth. Default: skip it, keep the deck in whatever slide-sharing system you use.

## 6. Data files — what to commit and what to gitignore

`data/` has grown to include AI-workload traces from the MIT Supercloud dataset. These are big and downloadable, so treat them as artifacts, not source.

| File | Size | Origin | Commit? |
|---|---|---|---|
| `data/gas_turbine_surrogate.csv` | 721 B | Phase 2 ThermoPower sweep | **Yes** — the surrogate the runtime imports |
| `data/load17.csv` (was `Load17`) | ~120 KB | Reference load trace consumed by notebooks | **Yes** — small, and needed to reproduce notebook results |
| `data/dcgm.csv` | 14 MB | Public S3 `mit-supercloud-dataset` | **No.** Regenerable via `tools/workload/mitsupercloud_download.py`. Document. |
| `data/scheduler_data.csv` | 70 MB | Public S3 `mit-supercloud-dataset` | **No.** Same reason. |
| `data/nvidia_smi_first_1gb.parquet` | 86 MB | Public S3 `mit-supercloud-dataset` | **No.** Same reason. |

Add a `data/README.md` in commit 11 explaining which files are tracked, which are downloaded by the workload tool, and what command to run.

## 7. The duplicate-CSV question (unchanged)

You have the surrogate in two places: `data/gas_turbine_surrogate.csv` (build output from Phase 2) and `gas_plant/data/gas_turbine_surrogate.csv` (bundled with the package).

Two options:
- **Option A (recommended):** Keep both, commit both. Root `data/` is the build artifact; `gas_plant/data/` is the runtime bundle. They diverge only if the surrogate is rebuilt — at which point a single commit `regen: refresh surrogate from sweep` updates both. No build step required to import `gas_plant`.
- **Option B:** Delete `gas_plant/data/`, have `pyproject.toml` copy from root `data/` at build time. Cleaner DRY-wise but adds a build dependency for a 2 KB file. Not worth it.

Take Option A.

## 8. Notebooks — outputs, nbstripout, and size

You now have seven notebooks totaling ~3.1 MB, with the two heaviest being `lm2500_model.ipynb` (1.9 MB) and `lm2500_ai_workload.ipynb` (1.1 MB). The bulk is embedded figure output.

Two viable positions:

- **Commit as-is.** Fastest, and the outputs *are* the deliverable for the tier studies — someone reviewing the tier plan wants to see the plots inline without re-running. Downside: every re-run creates a giant diff.
- **Set up `nbstripout`** as a git filter so `.ipynb` files are stored without outputs. Diffs stay tiny, reviewers re-run to see plots. Downside: reviewers who can't re-run see nothing.

Recommendation: **commit outputs for now** (commit 10), and add a `nbstripout` filter later once the tier work stabilizes and you're accumulating churn. If you *do* strip: run `nbstripout --install` in the working tree, then re-stage the notebooks, then commit.

If you go with commit-as-is, briefly check the two heaviest notebooks for anything that shouldn't be public (private paths, tokens, API keys, snippets of restricted PDFs) before committing.

## 9. `.gitignore`

Expanded from the original — now covers `.venv`, egg-info, editor backups, and the large workload downloads.

```
# Python
__pycache__/
*.pyc
.venv/
*.egg-info/
build/
dist/

# Conda
.conda/
envs/

# Modelica build droppings
*.mat
!tools/build_surrogate/libs/**/*.mat
*.bak-mo
tools/build_surrogate/work/
tools/build_surrogate/*.log

# Validation/example output (regenerable)
tools/build_surrogate/validation/
examples/output/

# Downloaded workload data (regenerable via tools/workload/mitsupercloud_download.py)
data/dcgm.csv
data/scheduler_data.csv
data/nvidia_smi_first_1gb.parquet
data/*.parquet
data/dcgm/
data/scheduler/

# Editor backups
*~
*.bak

# OS / editor
.DS_Store
.idea/
.vscode/
```

Notes:
- `examples/output/*.png` are regenerable from running the examples — ignored. If you want the reference plots in the docs, copy them into `docs/figures/` and commit those copies.
- The two workload-artifact `.parquet` and `.csv` entries are conservative — the broader `data/*.parquet` catches anything else the downloader emits. Keep `data/gas_turbine_surrogate.csv` tracked (it's not in the ignore list).

## 10. Things explicitly NOT to commit / NOT to fix

- `~/.condarc` typo (`confa-forge`) — out of repo scope, user-specific, leave alone.
- Don't squash the phase commits into one "initial commit" — the phase history is the value here.
- Don't add `andes` to the root `environment.yml`. It's a `gas_plant_andes` extra. The standalone-usage instruction is structurally enforced by this.
- Don't commit `ag-output.md` — it's a chat transcript, not a project artifact. Delete it.
- Don't commit `.venv/`, `gas_plant.egg-info/`, `__pycache__/`, `.DS_Store`, `*~`, `.bak`, or downloaded workload data — the gitignore covers them.
- Don't commit `mitsupercloud_pseudoCode.py` and `mitsupercloud_pseudoCode.py~` — the working `mitsupercloud_download.py` supersedes both. If you want the pseudocode preserved for provenance, move it to `tools/workload/archive/` instead of leaving it at root.
- Don't commit the inner `.git` directories inside `ThermoPower/` and `RAPS/` — remove them before the subtree add.

## 11. Execution — copy-paste commands

Run each block from `/Users/syellapa/Documents/Research/2026/DataCenter/`. Review each `git status` / `git diff --cached` output before committing. If a block errors, stop and diagnose before moving on — don't paste ahead.

### 11.0 — Preflight decisions (make these before typing any git command)

Four judgment calls to settle first:

- **RAPS (§4):** vendor as tracked files, or skip and reference upstream? Commands below assume vendor-and-track. If skipping, add `RAPS/` to `.gitignore` and drop commit-time references to it.
- **53 MB LM2500 training manual (§5):** commit raw, LFS, or skip-and-document? Commands below assume **skip-and-document** (Option A). If committing raw, add one extra `mv` in §11.1 and stage it in commit 8.
- **Notebook outputs (§8):** commit as-is, or run `nbstripout` first? Commands assume commit as-is.
- **`Load17` (§1):** confirm it's the reference load trace consumed by notebooks. If notebooks reference the filename `Load17` verbatim, either don't rename or update the notebooks in the same commit.

Also confirm your git identity is set globally (`git config --global user.name`, `user.email`) — the plan intentionally doesn't touch `git config`.

### 11.1 — Init, reshuffle, cleanup

```bash
cd /Users/syellapa/Documents/Research/2026/DataCenter/

# Sanity check: nothing weird lying around
ls -la

# Init repo (default-branch may be master; rename to main)
git init
git symbolic-ref HEAD refs/heads/main

# Capture vendored-repo provenance before removing nested .git dirs.
# Current known values in this workspace:
#   ThermoPower: e2b011ac7fd90f9cf5771f29f1aefa160550b6ee
#   RAPS:        879b2a4927377d6f92e1faad097162245d6817bc
THERMOPOWER_UPSTREAM_HASH=$(git -C ThermoPower log -1 --format=%H 2>/dev/null || true)
RAPS_UPSTREAM_HASH=$(git -C RAPS log -1 --format=%H 2>/dev/null || true)
printf 'ThermoPower upstream hash: %s\n' "$THERMOPOWER_UPSTREAM_HASH"
printf 'RAPS upstream hash: %s\n' "$RAPS_UPSTREAM_HASH"

# Create the target directory structure
mkdir -p docs refs/lm2500 refs/lm9000 tools/workload

# Move phase docs and root markdowns to docs/
mv phase1_setup.md phase2_3_setup.md phase4_5_setup.md phase6_setup.md docs/
mv electrical_transient_modeling.md andes_coupling_benefits.md docs/
mv lm2500_plan.md docs/lm2500_plan.md
mv LM9000_IMPLEMENTATION_SUMMARY.md docs/lm9000_implementation_summary.md
mv gTdynamics.txt docs/gt_dynamics_notes.md

# Move workload scripts to tools/workload/
mv mitsupercloud_power.py tools/workload/mitsupercloud_download.py
mv mitsupercloud_energy.txt tools/workload/mitsupercloud_notes.md

# Move Load17 (confirm it's a load trace first — see §11.0)
mv Load17 data/load17.csv

# Update notebooks that read the old root-level Load17 path.
# This is required if you rename Load17; otherwise commit 10 notebooks will fail in a fresh clone.
python - <<'PY'
from pathlib import Path

for path in Path('notebooks').glob('*.ipynb'):
    text = path.read_text()
    new = text.replace("ROOT / 'Load17'", "ROOT / 'data' / 'load17.csv'")
    new = new.replace('ROOT / "Load17"', 'ROOT / "data" / "load17.csv"')
    if new != text:
        path.write_text(new)
        print(path)
PY

# Move reference PDFs into refs/
mv "LM9000/LM9000 Overview 042225.pdf" refs/lm9000/LM9000_Overview_042225.pdf
rmdir LM9000
mv lm2500-pocketguide.pdf refs/lm2500/
# If Option A (skip 53 MB manual): leave MSC01A_*.pdf at root for now, don't stage it.
# If Option C (commit raw): also run
#   mv MSC01A_1211_B5_LM2500-Gas-Turbine_-r5b_rg.pdf refs/lm2500/

# Drop the pptx unless you're versioning slides — see §5
# mv Presentation/GT-LM2500.pptx refs/lm2500/    # only if committing it
# rmdir Presentation                              # only if not committing it

# Delete stray files and editor backups
rm ag-output.md
rm mitsupercloud_pseudoCode.py mitsupercloud_pseudoCode.py~ mitsupercloud_energy.txt~
rm -f papers/.DS_Store .DS_Store

# Preserve pseudocode in an archive if you want provenance (§10)
# mkdir -p tools/workload/archive
# git mv <path>  ... (only if you kept the file above)

# Remove inner .git dirs so vendored trees are tracked as normal files
rm -rf ThermoPower/.git
rm -rf RAPS/.git   # skip if you decided not to vendor RAPS

# Sanity check
ls -la
ls docs/ refs/lm2500/ refs/lm9000/ tools/workload/
```

### 11.2 — Write `.gitignore`

Create `.gitignore` at repo root with the exact contents from §9. Use your editor or:

```bash
cat > .gitignore <<'EOF'
# Python
__pycache__/
*.pyc
.venv/
*.egg-info/
build/
dist/

# Conda
.conda/
envs/

# Modelica build droppings
*.mat
!tools/build_surrogate/libs/**/*.mat
*.bak-mo
tools/build_surrogate/work/
tools/build_surrogate/*.log

# Validation/example output (regenerable)
tools/build_surrogate/validation/
examples/output/

# Downloaded workload data (regenerable via tools/workload/mitsupercloud_download.py)
data/dcgm.csv
data/scheduler_data.csv
data/nvidia_smi_first_1gb.parquet
data/*.parquet
data/dcgm/
data/scheduler/

# Editor backups
*~
*.bak

# OS / editor
.DS_Store
.idea/
.vscode/
EOF
```

Verify the ignored artifacts aren't accidentally about to be staged:

```bash
git status --ignored | head -40
```

You should see `.venv/`, `gas_plant.egg-info/`, `__pycache__/`, and the three large `data/` files listed under "Ignored files".

### 11.3 — Commit 0: scaffolding

Empty out `gas_plant/__init__.py` and stub the README before staging (commits 3/4/7 will re-populate `__init__.py` progressively).

```bash
# Save the final __init__.py content for later re-population
cp gas_plant/__init__.py /tmp/gas_plant_init_final.py

# Reset __init__.py to empty
: > gas_plant/__init__.py

# Minimal README stub (skip if you already have real content — check first)
[ -f README.md ] || cat > README.md <<'EOF'
# DataCenter

Power-generation surrogate models and dynamics for data-center workload studies. See `docs/` for phase and tier documentation.
EOF

git add .gitignore environment.yml pyproject.toml gas_plant/__init__.py README.md
git status
git commit -m "chore: repo scaffolding"
```

### 11.4 — Commit 1: phase 1 (OpenModelica + MSL + ThermoPower)

```bash
cat > tools/build_surrogate/libs/README.md <<'EOF'
# Vendored Modelica libraries

This directory contains the Modelica Standard Library and companion libraries
used by the offline OpenModelica surrogate build.

- Source: https://github.com/modelica/ModelicaStandardLibrary
- Version: v3.2.3+build.4

The libraries are committed for reproducibility because OpenModelica package
downloads can fail in offline or SSL-constrained environments.
EOF

git add tools/build_surrogate/libs/ \
        tools/build_surrogate/load_check.mos \
        docs/phase1_setup.md \
        ThermoPower/
git status
git commit -m "phase 1: openmodelica via docker + vendored MSL + ThermoPower"
```

### 11.5 — Commit 2: phase 2 (parameter sweep → CSV surrogate)

```bash
git add tools/build_surrogate/sweep_gt.mos \
        tools/build_surrogate/validate_gt.mos \
        tools/build_surrogate/validate_surrogate.py \
        data/gas_turbine_surrogate.csv \
        docs/phase2_3_setup.md
git status
git commit -m "phase 2: gas-turbine parameter sweep → CSV surrogate"
```

### 11.6 — Commit 3: phase 3 (Python GasTurbinePlant + Fleet)

Populate `__init__.py` with just the phase-3 exports:

```bash
cat > gas_plant/__init__.py <<'EOF'
from .unit import GasTurbinePlant
from .fleet import Fleet

__all__ = ["GasTurbinePlant", "Fleet"]
EOF

git add gas_plant/__init__.py \
        gas_plant/unit.py \
        gas_plant/fleet.py \
        gas_plant/data/gas_turbine_surrogate.csv \
        tests/smoke_test.py
git status
git commit -m "phase 3: python GasTurbinePlant + Fleet"
```

_Note: `tests/smoke_test.py` currently contains gas + CCPP tests together. That's a small fudge — the file gets committed once here rather than split across commits 3 and 5. Acceptable._

### 11.7 — Commit 4: phase 4 (analytical CCPP layer)

Extend `__init__.py`:

```bash
cat > gas_plant/__init__.py <<'EOF'
from .unit import GasTurbinePlant
from .combined_cycle import CombinedCyclePlant
from .fleet import Fleet

__all__ = ["GasTurbinePlant", "CombinedCyclePlant", "Fleet"]
EOF

git add gas_plant/__init__.py \
        gas_plant/combined_cycle.py \
        tools/build_surrogate/check_ccpp.mos \
        tools/build_surrogate/validate_ccpp.py \
        docs/phase4_5_setup.md
git status
git commit -m "phase 4: analytical CCPP layer"
```

### 11.8 — Commit 5: phase 5 (packaging polish)

This commit should not be skipped unless you have already fixed packaging. The current `pyproject.toml` package list only installs `gas_plant`, which omits `gas_plant.dynamics` and `gas_plant_andes` from an installed distribution. Switch to package discovery and add `andes` as an optional extra; do **not** add ANDES to base `environment.yml`.

```bash
python - <<'PY'
from pathlib import Path

path = Path('pyproject.toml')
text = path.read_text()
old = '''[tool.setuptools]\npackages = ["gas_plant"]\n\n[tool.setuptools.package-data]\ngas_plant = ["data/*.csv"]\n'''
new = '''[project.optional-dependencies]\nandes = ["andes"]\n\n[tool.setuptools.packages.find]\ninclude = ["gas_plant", "gas_plant.*", "gas_plant_andes", "gas_plant_andes.*"]\n\n[tool.setuptools.package-data]\ngas_plant = ["data/*.csv"]\n'''
if old not in text:
        raise SystemExit('Expected pyproject.toml setuptools block not found; edit packaging manually before commit 5.')
path.write_text(text.replace(old, new))
PY

git add pyproject.toml README.md tests/smoke_test.py
git status
git diff --cached
git commit -m "phase 5: packaging + smoke tests"
```

Tag the steady-state release after commit 5:

```bash
git tag -a v0.1.0 -m "v0.1.0: steady-state gas + CCPP surrogate, no ANDES"
```

### 11.9 — Commit 6: phase 6 (ANDES electrical coupling)

```bash
git add gas_plant_andes/__init__.py \
        gas_plant_andes/case_builder.py \
        gas_plant_andes/defaults.py \
        gas_plant_andes/scenarios.py \
        examples/example_islanding.py \
        examples/example_resiliency.py \
        docs/electrical_transient_modeling.md \
        docs/andes_coupling_benefits.md \
        docs/phase6_setup.md
git status
git commit -m "phase 6: ANDES electrical-dynamics coupling"

git tag -a v0.2.0 -m "v0.2.0: adds gas_plant_andes electrical coupling"
```

### 11.10 — Commit 7: LM9000 first-principles model

Extend `__init__.py` to the final state (matches `/tmp/gas_plant_init_final.py` you saved in §11.3):

```bash
cat > gas_plant/__init__.py <<'EOF'
from .unit import GasTurbinePlant
from .combined_cycle import CombinedCyclePlant
from .lm9000 import LM9000SimpleCycle, LM9000GasTurbine, LM9000CombinedCycle
from .fleet import Fleet

__all__ = ["GasTurbinePlant", "CombinedCyclePlant", "LM9000SimpleCycle", "LM9000GasTurbine", "LM9000CombinedCycle", "Fleet"]
EOF

# Verify it matches what the code actually needs
diff gas_plant/__init__.py /tmp/gas_plant_init_final.py

git add gas_plant/__init__.py \
        gas_plant/lm9000.py \
        docs/lm9000_implementation_summary.md \
        refs/lm9000/LM9000_Overview_042225.pdf
git status
git commit -m "lm9000: first-principles two-spool model + docs"
```

### 11.11 — Commit 8: LM2500 dynamics tier plan + references

If you're **skipping the 53 MB LM2500 manual** (§11.0 Option A), stub a README so the reference is at least traceable:

```bash
cat > refs/lm2500/README.md <<'EOF'
# LM2500 reference materials

## Not committed to the repo

- `MSC01A_1211_B5_LM2500-Gas-Turbine_-r5b_rg.pdf` (53 MB) —
  Military Sealift Command LM2500 Combustion Turbine Course,
  Technical Training Professionals, 2021. Source-of-truth for
  the LM2500 generator rating (23.0 MW), heat rate (8,746 BTU/kW-hr),
  and other calibration parameters used in `docs/lm2500_plan.md`.
  Kept off-repo due to size; obtain from <your storage location>.

## Committed

- `lm2500-pocketguide.pdf` — quick-reference summary of LM2500 operating envelope.
EOF
```

Stage the commit:

```bash
git add docs/tier_plan.md \
        docs/lm2500_plan.md \
        docs/gt_dynamics_notes.md \
        papers/ \
        refs/lm2500/README.md \
        refs/lm2500/lm2500-pocketguide.pdf

# If you chose Option C (commit the 53 MB manual raw):
# git add refs/lm2500/MSC01A_1211_B5_LM2500-Gas-Turbine_-r5b_rg.pdf

# If you decided to version the pptx:
# git add refs/lm2500/GT-LM2500.pptx

git status
git commit -m "docs: lm2500 dynamics tier plan + reference papers"
```

### 11.12 — Commit 9: dynamics subpackage (Tier A/B/C/E)

```bash
git add gas_plant/dynamics/__init__.py \
        gas_plant/dynamics/tgov1.py \
        gas_plant/dynamics/ggov1.py \
        gas_plant/dynamics/multishaft.py \
        gas_plant/dynamics/torsional.py
git status
git commit -m "dynamics: tier A/B/C/E prime-mover + governor models"

git tag -a v0.3.0 -m "v0.3.0: adds LM9000 model and dynamics subpackage"
```

### 11.13 — Commit 10: notebooks

Quick sanity pass before staging — check the two heaviest for anything you don't want public:

```bash
# Scan for absolute local paths and possible secrets
grep -l "/Users/syellapa" notebooks/*.ipynb || echo "no user-path leaks"
grep -l -iE "aws_secret|api_key|password|token" notebooks/*.ipynb || echo "no obvious secrets"
```

If the path scan reports hits, clean them before staging. Expected fixes are:
- replace root-path code cells such as `Path('/Users/syellapa/Documents/Research/2026/DataCenter')` with repo-relative discovery (`Path.cwd()` or the notebook's existing `ROOT` helper);
- clear or rerun output cells that print `/Users/syellapa/...`, including old `Load17` paths;
- keep output clearing scoped to the affected notebooks so the rest of the committed results remain intact.

Stage and commit:

```bash
git add notebooks/lm2500_model.ipynb \
        notebooks/lm2500_ai_workload.ipynb \
        notebooks/lm2500_tierA_tgov1_fixes.ipynb \
        notebooks/lm2500_tierB_ggov1.ipynb \
        notebooks/lm2500_tierC_multishaft.ipynb \
        notebooks/lm2500_tierE_torsional.ipynb \
        notebooks/lm9000_model.ipynb
git status
git commit -m "notebooks: lm2500 baseline + tier A/B/C/E studies + lm9000"
```

### 11.14 — Commit 11: workload downloader + reference trace

```bash
cat > data/README.md <<'EOF'
# data/

## Tracked
- `gas_turbine_surrogate.csv` — Phase 2 ThermoPower sweep output.
- `load17.csv` — reference load trace used by LM2500 dynamics notebooks.

## Not tracked (regenerable)
Downloaded on demand by `tools/workload/mitsupercloud_download.py` from the
public S3 bucket `s3://mit-supercloud-dataset/`:
- `dcgm.csv` — GPU power telemetry (~14 MB)
- `scheduler_data.csv` — job scheduler traces (~70 MB)
- `nvidia_smi_first_1gb.parquet` — nvidia-smi samples (~86 MB)

Run the downloader from repo root; see `tools/workload/mitsupercloud_notes.md`.
EOF

git add tools/workload/mitsupercloud_download.py \
        tools/workload/mitsupercloud_notes.md \
        data/load17.csv \
        data/README.md
# If you kept the pseudocode:
# git add tools/workload/archive/mitsupercloud_pseudoCode.py
git status
git commit -m "tools: mit-supercloud workload downloader + reference load trace"
```

### 11.15 — RAPS (only if you chose to vendor it — §4)

If you decided in §11.0 to vendor RAPS, commit it as its own step so the vendor drop is obvious in history. Use the `RAPS_UPSTREAM_HASH` captured in §11.1 before `RAPS/.git` was removed.

```bash
git add RAPS/
git status
git commit -m "vendor: ExaDigiT/RAPS at upstream commit ${RAPS_UPSTREAM_HASH:-879b2a4927377d6f92e1faad097162245d6817bc}"
```

If you did not preserve the shell variable, use the printed value from §11.1. In this workspace that value was `879b2a4927377d6f92e1faad097162245d6817bc`.

### 11.16 — Final review, remote, push

```bash
# One-line-per-commit history
git log --oneline --decorate

# What's in the repo (should show gas_plant/, gas_plant_andes/, tools/, ThermoPower/,
# RAPS/, papers/, refs/, docs/, notebooks/, examples/, tests/, data/)
git ls-tree -r HEAD --name-only | head -50
git ls-tree -r HEAD --name-only | wc -l

# Repo size on disk
du -sh .git
git count-objects -vH

# Check for any file > 10 MB that ended up tracked
git ls-files | xargs -I{} du -h {} 2>/dev/null | awk '$1 ~ /M/ && $1+0 > 10 {print}'
```

If a large blob shows up that you didn't intend to commit, fix it **before** pushing:

```bash
# Untrack (keeps on disk) and amend the offending commit
git rm --cached <path>
# Add <path> to .gitignore, then:
git add .gitignore
git commit --amend --no-edit
```

Create the remote and push (adjust `<owner>/<repo>` and visibility):

```bash
# GitHub via gh CLI
gh repo create <owner>/DataCenter --private --source=. --remote=origin --push
git push origin --tags

# ...or manually if remote already exists
# git remote add origin git@github.com:<owner>/DataCenter.git
# git push -u origin main
# git push origin --tags
```

### 11.17 — Post-push verification

```bash
# On the remote, confirm no LFS-warning banners and no >50 MB files
gh repo view --web

# Local clone check — does a fresh clone pass smoke tests?
cd /tmp
git clone git@github.com:<owner>/DataCenter.git DataCenter-verify
cd DataCenter-verify
python -c "import gas_plant; print(gas_plant.__all__)"
```

If GitHub flags any large blob after the push, either LFS-migrate that path (`git lfs migrate import --include="refs/lm2500/MSC01A_*.pdf" --include-ref=refs/heads/main`) or remove it with `git filter-repo` and force-push. **Force-pushing to `main` counts as destructive** — since it's the initial push and no collaborators are involved yet, it's low-risk, but do it deliberately and re-check §11.16 first.

---

Nothing above has been executed yet. Execute block-by-block, reviewing `git status` and `git diff --cached` between blocks. When in doubt on the §11.0 judgment calls, stop and decide before staging that commit — those are the reversibility-costly ones.

# Phase 1 setup — what worked

Operational reference for re-creating the OpenModelica toolchain on macOS (Apple Silicon). Last verified 2026-05-21 with OM 1.26.7.

## Final working toolchain

```bash
# 1. Container runtime (Lima VM under the hood, no Docker Desktop)
brew install colima docker
colima start

# 2. OpenModelica image (headless, arm64-native, ~270 MB)
docker pull openmodelica/openmodelica:v1.26.7-minimal

# 3. MSL 3.2.3 vendored locally (one-time, ~17 MB extracted)
#    See tools/build_surrogate/libs/ — directories are named "<Library> <version>/"
#    as the OPENMODELICALIBRARY resolver expects.

# 4. Canonical invocation — bind-mount project, point omc at the vendored libs
docker run --rm \
  -v "$PWD:/work" -w /work \
  -e OPENMODELICALIBRARY=/work/tools/build_surrogate/libs \
  openmodelica/openmodelica:v1.26.7-minimal \
  omc /work/tools/build_surrogate/<script>.mos
```

Validation: `checkModel(ThermoPower.PowerPlants.GasTurbine.Examples.GasTurbineSimplified)` returns "Check completed successfully. 38 equations / 38 variables / 30 trivial."

## Pitfalls and how they were resolved

| Symptom | Root cause | Fix |
| --- | --- | --- |
| `brew install --cask openmodelica` → "No Cask with this name exists" | Native macOS OpenModelica builds discontinued after 1.16. No Homebrew cask exists. | Use the official Docker image instead — the supported path on Apple Silicon per the OpenModelica download page. |
| `docker pull openmodelica/openmodelica:latest` → "not found" | This image is published with version-tagged images only; `latest` is never set. | Pin a specific tag, e.g. `v1.26.7-minimal`. The `-minimal` variant is sufficient for headless `omc` use (no GUI). |
| `loadModel(Modelica, {"3.2.3"})` → "Curl error … SSL peer certificate or SSH remote key was not OK" against `libraries.openmodelica.org` | The minimal image doesn't bundle MSL; `omc`'s embedded libcurl/CA stack fails the HTTPS handshake even though the container's network and CAs are fine (`wget` against the same host works). | Vendor MSL 3.2.3 to `tools/build_surrogate/libs/` from the GitHub release tarball (`v3.2.3+build.4`). Set `OPENMODELICALIBRARY=/work/tools/build_surrogate/libs` on the container so `loadModel(Modelica, {"3.2.3"})` resolves locally. No network in the omc path. |
| `pkgconf` "could not symlink" warning during `brew install --cask openmodelica` attempt | Unrelated side effect — brew reinstalled `pkgconf` as a dep during the failed cask attempt, and it conflicted with an existing `pkg-config` symlink. | Ignored — does not block subsequent installs. Brew suggests `brew link --overwrite pkgconf` if it ever matters. |

## Repo layout this established

```
DataCenter/
├── ThermoPower/                         # vendored Modelica library (unchanged)
├── tools/
│   └── build_surrogate/
│       ├── libs/                        # vendored Modelica libraries
│       │   ├── Complex 3.2.3.mo
│       │   ├── Modelica 3.2.3/
│       │   └── ModelicaServices 3.2.3/
│       └── load_check.mos               # smoke test for the toolchain
└── phase1_setup.md                      # this file
```

## Adding more Modelica libraries later

Drop them into `tools/build_surrogate/libs/` using the same `<LibraryName> <version>/` directory naming. `OPENMODELICALIBRARY` already points there, so no other config changes are needed.

## Daily use after the first install

`colima` persists between reboots but the VM may need a restart:

```bash
colima status            # is the VM up?
colima start             # if not
# ... then docker run as above ...
colima stop              # optional, frees host resources
```

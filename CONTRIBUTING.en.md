# Contributing

[中文](CONTRIBUTING.md) | English

Welcome to **Ascend** — an AI-native simulation platform that constructs causal worlds bottom-up.

This document is written for first-time contributors to this project, helping you understand the project's positioning, design principles, and the conventions for development, testing, committing, and releasing.

## Positioning

Ascend serves a dual purpose:

- **Research platform**: a reproducible, intervenable, traceable causal world that produces spatiotemporal sequences for world model training
- **Game**: the player is an individual steering population evolution through genetic engineering; NPCs are dynamically driven by AI

## Design Principles (Mandatory)

Before you start, please make sure your changes do not violate the following principles:

### Deep Modules, Loose Coupling

Modules expose only **small, deep interfaces** and hide their internal complexity, leaking no implementation details. Callers need only understand the interface contract.

### No Patch-Style Code

Never write hacks that work around a problem. When you hit a bug, locate the **root cause** first and fix it systematically. Patch-style code masks problems and accumulates technical debt.

### Frontend/Backend Separation

The backend owns logic; the frontend owns rendering, UI, input, and audio. The two communicate only through the protocol (currently JSON over TCP) and never call each other directly.

### Data and Algorithms Decoupled

Data is data, algorithms are algorithms; each evolves independently without coupling. Changing a data definition should not ripple through related algorithms, and vice versa.

### No Backward Compatibility Promised

There is no legacy burden at this stage. Refactoring takes priority; interface changes are allowed. Do not keep redundant design just for backward compatibility.

## Development Workflow

When developing a new module, follow this fixed order:

1. **Clarify the requirement**: understand what problem is being solved
2. **Define the interface**: settle the module's external contract
3. **Write tests first**: encode expected behavior
4. **Implement**: write code until tests pass

## World Content Data (JSON)

Tunable content (terrain, biome, climate, weather, world-gen parameters) lives
as JSON in `data/` (`terrain.json`/`climate.json`/`biome.json`/`weather.json`/
`world.json`) — **change content by editing the data file, no code changes**.
Conventions:

- Keys are namespaced ids (e.g. `ascend:grassland`); `value` is explicit,
  unique, and contiguous 0..n-1 — **published values are immutable, append-only**
  (renumbering breaks saves/caches)
- Display names store only `label_key` (e.g. `terrain.grassland`); text lives in
  `lang/*.json` (zh/en stay in sync, verified by a test)
- Adding content = append an entry to the data file (`value` next + `label_key`);
  import-time validation plus tests guard the contracts; packaging ships `data/`
  and `lang/` automatically

## Testing

### Backend (Python / pytest)

After every code change, run the affected unit tests (no need to run the full suite during day-to-day development):

```bash
.venv/bin/python -m pytest --testmon -n 4 -q
```

- `-n 4` is a deliberately conservative degree of parallelism: with `-n auto`, workers are spawned to match the machine's core count, which can OOM/saturate the CPU during world generation. This command is for unit tests only
- Integration tests must run serially (port/subprocess conflicts): `.venv/bin/python -m pytest testbench/integration -v`
- No need to run the full suite locally; CI's `test` job runs it automatically before release

### Frontend (GDScript / GUT)

```bash
cd miskhak/client && ./run_tests.sh unit
```

GUT is not distributed with the repo (`miskhak/client/addons/gut` only needs to be installed locally), so frontend tests run only locally.

### Research declaration pipeline (drift gates)

The single source of truth for research equations is the module declarations in
`olam/modules/` (six declaration kinds + implementation bindings);
the research-side projection is `kheker/equations/equations.json`. After modifying
any equation, parameter, node declaration, or `data/*.json`, you must regenerate and
commit the generated artifacts, or the CI drift gates will fail:

```bash
.venv/bin/python kheker/equations/export_registry.py          # world declarations → equations.json
.venv/bin/python kheker/equations/gen_lean.py                 # equations.json → Lean data section
.venv/bin/python kheker/equations/export_impl_digests.py      # implementation digest table (packaged identity)
.venv/bin/python kheker/equations/export_frozen_tables.py     # precomputed tables (only when the table spec changes)
.venv/bin/python kheker/equations/verify_equations.py --fast  # full reconciliation (V0–V4)
.venv/bin/python kheker/equations/graph_check.py              # graph health checks (G0–G7)
```

`equations.json`, `GenDeclarationData.lean`, `impl_digests.json`, and
`olam/kernel/frozen_tables.py` are generated artifacts — never edit them by hand.

**Intervention executor** (see the World Contract, WC-6): researcher interventions are
registered through `olam/protocols/timeline.py` as plans plus append-only per-frame
records, and replace the generated value at evaluation points. Timeline validation
uses the compiled program as the single source of truth (slot exists and is
mechanism-written, permissions/domains/instances, parameters consumed by wired
mechanisms); `testbench/world/test_timeline.py` and the wiring drift tests guard it.

**Acceptance runner**: after changing a declaration or engine path, run
`.venv/bin/python kheker/acceptance/run_acceptance.py`
(C0–C2/W0–W7/I0–I1/L3; any failing criterion turns CI red). After changing a declaration, also
run `run_acceptance.py --check` to detect Lean UnrolledDag instance drift (including the
machine-checked `WellFormed`).

**Research records** (see the World Contract, WC-10): the research log and gameplay
events live in separate stores — new mechanisms are recorded automatically; never put
record fields into event payloads (a gate test enforces this); evaluation records go
through the `TraceLog` in `olam/protocols/records.py`, captured per mechanism when the
engine mounts a log.

**Complete save** (see the World Contract, WC-7.5 and WC-8): when adding runtime
state that cannot be recomputed from the world settings (researcher-applied quantities,
markers that evolve with the mechanisms), update the `state.json.enc` payload and the
restore path (`save/serializer.py` plus the subsystem's `persist_*` / `restore_*`) and
add the W4 dual-run equality assertion. Recomputable analytic quantities must **not** be
persisted. Bump `STATE_VERSION` when the payload format changes — old saves become
unreadable by design; no migration is written.

## Commit Conventions

Follow the [Conventional Commits](https://www.conventionalcommits.org/) spec and write your commit descriptions in Chinese.

## Contribution License

- Before opening a Pull Request, please read and agree to [CLA.md](CLA.md) (Contributor License Agreement) — it authorizes the maintainer to commercially license and distribute software incorporating your contributions
- Checking the "Contributor confirmation" box in the PR template counts as agreement to the CLA
- CI verifies this automatically via [CLA Check](.github/workflows/cla.yml); PRs without agreement are flagged as not mergeable
- If your contribution includes third-party material (code, libraries, assets, etc.), please note its source and applicable license in the PR
- The project is licensed under [CC BY-NC-SA 4.0](LICENSE); your contributions are also publicly released under that license

## Release

- Single source of version: `build/version/` (`core.txt` shared core, plus `miskhak.txt` / `kheker.txt` product versions = core version + product sequence; verified by `build/ci/check_version.sh`)
- Pushing a `research-v*` tag triggers CI auto-release of the research package; this is normally done by maintainers:

```bash
git push origin main
git tag research-v<version> && git push origin research-v<version>
```

- Local build: `bash build/package.sh miskhak|kheker [linux|windows|all]` (see `build/README.md`)
- Game packages (including proprietary assets) follow a private process, not this repo's CI

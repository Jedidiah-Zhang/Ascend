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

The backend owns world and agent logic; the frontend owns rendering, UI, input, and audio, with responsibilities separated through explicit interfaces. The current implementation uses JSON over TCP. Bindings and transport for the complete rewrite remain undecided; follow confirmed decisions in [Overall Architecture](docs/整体架构.md).

### Data and Algorithms Decoupled

Data is data, algorithms are algorithms; each evolves independently without coupling. Changing a data definition should not ripple through related algorithms, and vice versa.

### No Backward Compatibility Promised

There is no legacy burden at this stage. Refactoring takes priority; interface changes are allowed. Do not keep redundant design just for backward compatibility.

This principle concerns development interfaces and refactoring, not the new architecture's game-save compatibility goal. See [Mod Management](docs/游戏平台/模组管理/设计.md) for continuing games after mod changes.

## Development Workflow

The world and game architecture is being redesigned. See the [World](docs/世界/综述.md), [Agents](docs/智能体/综述.md), [Game Platform](docs/游戏平台/综述.md), and [Research Platform](docs/研究平台/综述.md) overviews for module topics and discussion order. Open design questions are not implementation requirements; the [Archived Design](docs/归档/README.md) is for historical reference. The data, testing, and release instructions below continue to apply to the existing implementation and will be updated as designs are confirmed.

When developing a new module, follow this fixed order:

1. **Clarify the requirement**: understand what problem is being solved
2. **Define the interface**: settle the module's external contract
3. **Write tests first**: encode expected behavior
4. **Implement**: write code until tests pass

## Documentation Maintenance

This section is the maintenance entry for engineering documentation conventions. It applies to [Overall Architecture](docs/整体架构.md) and the world, agents, game-platform, and research-platform partitions. The author-written [Research Overview](docs/研究理论/研究综述.md) is the highest research authority and is preserved unchanged. Other former research materials are archived; specific research constraints and validation plans must be reconsidered.

### Responsibilities and Single Sources

| Document | Responsibility |
| --- | --- |
| `docs/整体架构.md` | Cross-partition responsibilities, library/platform boundaries, dependencies, and assembly principles; link to module details |
| Parent `综述.md` | Responsibilities, child navigation, dependencies, and design progress; do not duplicate complete leaf rules |
| Leaf `设计.md` | Goals, concepts, behavior, interface semantics, configuration, tradeoffs, acceptance, and open questions |
| Leaf `实现.md` | Implementation proposals, actual delivery, code/build entry points, tests, and performance evidence |
| Research Overview | Highest research authority; subsequent constraints, formalization, and protocols must be discussed on this basis, without precedence for the old contract |
| Archives | Historical material, not current implementation requirements |

- Each rule has one owning document. Other documents summarize and link to it. Ownership follows responsibility, not whichever file is higher-level, newer, or more strongly worded.
- World documents define general capabilities and mechanisms; gameplay and save-continuation policies belong to the game platform, experiment locking and research validation to the research platform, and cognition implementation to agents.
- Record differences between the Research Overview and engineering scope, and unresolved mappings, in the research platform. Do not rewrite the overview or copy old clauses to hide a conflict. Archived research proposals require explicit discussion before reuse.
- Keep the module hierarchy: parent overviews, paired leaf design/implementation files. Unstarted topics can remain in the parent inventory; do not mass-create empty directories.

### Separate Status Dimensions

- **Design status**: `待讨论` (topics only), `讨论中` (some rules settled), `基础确定` (responsibility and core behavior available to depend on), `可实施` (interfaces, edge cases, and acceptance sufficient for the stated scope). Paused discussion is an annotation, not completion.
- **Decision status**: `已确认` (confirmed requirement), `暂定` (provisional basis with an explicit review condition), `候选` (comparison only). Collect open decisions separately.
- **Implementation status**: `未实施` (not implemented), `部分实施` (partial), `已实现` (implemented). State verification scope and evidence; matching legacy code names do not prove the new design is implemented.
- A commit, move, formatting pass, change of discussion topic, or lack of objection does not approve a candidate. Editors must not promote recommendations to confirmed rules without an explicit design decision.

Headers state status, scope, and navigation. Design headers also identify settled and uncovered scope. Implementation files link to design rather than duplicating its status. Parent progress summaries must agree with child documents.

### Leaf Templates

Order `设计.md` as follows; combine short sections where useful. Keep unstarted documents brief rather than inventing content to fill a template:

1. **Goals and responsibilities**: problem, use cases, and neighboring responsibilities.
2. **Concepts and terminology**: objects, state, and definitions.
3. **Confirmed rules**: define each rule once by topic; label provisional decisions and their review conditions separately.
4. **External contracts**: inputs, outputs, preconditions, effects, effective timing, and failure behavior; semantics before signatures.
5. **Configuration and edge cases**: units, ranges, defaults, mutability, and applicable exceptional cases.
6. **Tradeoffs and candidates**: reasons, costs, alternatives, and undecided parts.
7. **Acceptance criteria**: checkable conditions, operations, and expectations; distinguish candidate checks from confirmed rules.
8. **Open questions**: impact, whether implementation is blocked, and the responsible module or phase.

Order `实现.md` as: **Design basis → Implementation proposals → Current delivery → Code/build entry points → Verification/performance**. Separate proposed work, actual facts, and gaps. State when code or measurements do not exist; never invent paths, commands, or results.

### Quality and Maintenance Workflow

- Important rules may have stable module-local IDs for contracts and acceptance references; not every paragraph needs one. Mark illustrative values so they cannot be mistaken for defaults.
- Replace vague goals such as "deterministic" or "fast" with scoped behavior or measurement targets. Ordinary engineering acceptance does not automatically require research proofs.
- Record candidates during discussion; update the owning rule when confirmed; consolidate duplicates, obsolete candidates, and conversational corrections at milestones, retaining relevant rationale. Git preserves ordinary history; use separate decision records only when justified.
- Implementation changes update delivery and evidence; design changes review consumers. Moves repair relative links and update parent navigation and relevant bilingual entry points.
- Documentation-only changes check whitespace diffs, local links and anchors, navigation, and status consistency. Verify preserved research/archive contents when promised. Formatting checks do not establish semantic correctness; avoid unrelated code tests for documentation changes.

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

The research declaration pipeline and WC references below maintain the existing implementation under the [archived World Contract](docs/归档/研究理论/世界契约.md). Passing legacy checks does not establish research requirements or completion for the new architecture.

### Backend (Python / pytest)

After every code change, run the affected unit tests (no need to run the full suite during day-to-day development):

```bash
.venv/bin/python -m pytest --testmon -n 4 -q
```

- `-n 4` is a deliberately conservative degree of parallelism: with `-n auto`, workers are spawned to match the machine's core count, which can OOM/saturate the CPU during world generation. This command is for unit tests only
- Integration tests must run serially (port/subprocess conflicts): `.venv/bin/python -m pytest testbench/integration -v`
- No need to run the full suite locally; CI's `test` job runs it automatically before release

### Headless C++ declaration engine

The new core in `kheker/native/` uses C++17, CMake 3.20, and CTest without Qt. After changes, run from the repository root:

```bash
cmake -S kheker/native -B build/work/native -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON
cmake --build build/work/native --config Debug --parallel 4
ctest --test-dir build/work/native --build-config Debug --output-on-failure
```

Tests live in `testbench/native/`; build outputs go under the ignored `build/work/` directory. [Native Engine CI](.github/workflows/native_engine.yml) builds the same suite on Linux, Windows, and macOS. Local verification results are recorded in the [implementation document](docs/研究平台/实验环境与声明接入/实现.md#5-验证与性能).

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

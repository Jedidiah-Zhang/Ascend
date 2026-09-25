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

World and agent implementations are separated from client and platform responsibilities. Follow confirmed decisions in [Overall Architecture](docs/整体架构.md) and the owning module designs for bindings and transport.

### Data and Algorithms Decoupled

Data is data, algorithms are algorithms; each evolves independently without coupling. Changing a data definition should not ripple through related algorithms, and vice versa.

### No Backward Compatibility Promised

Define responsibilities and contracts for the new architecture first. Development interfaces evolve according to confirmed design without adding unspecified compatibility requirements.

This principle concerns development interfaces and refactoring, not the new architecture's game-save compatibility goal. See [Mod Management](docs/游戏平台/模组管理/设计.md) for continuing games after mod changes.

## Development Workflow

Development follows the [Overall Architecture](docs/整体架构.md). See the [World](docs/世界/综述.md), [Agents](docs/智能体/综述.md), [Game Platform](docs/游戏平台/综述.md), and [Research Platform](docs/研究平台/综述.md) overviews for current plans and progress. Open design questions are not implementation requirements.

When developing a new module, follow this fixed order:

1. **Clarify the requirement**: understand what problem is being solved
2. **Define the interface**: settle the module's external contract
3. **Write tests first**: encode expected behavior
4. **Implement**: write code until tests pass

## Documentation Maintenance

This section is the maintenance entry for engineering documentation conventions. It applies to [Overall Architecture](docs/整体架构.md) and the world, agents, game-platform, and research-platform partitions. The author-written [Research Overview](docs/研究理论/研究综述.md) is the highest research authority; the research platform records specific constraints and validation plans.

### Responsibilities and Single Sources

| Document | Responsibility |
| --- | --- |
| `docs/整体架构.md` | Cross-partition responsibilities, library/platform boundaries, dependencies, and assembly principles; link to module details |
| Parent `综述.md` | Responsibilities, child navigation, dependencies, and design progress; do not duplicate complete leaf rules |
| Leaf `设计.md` | Goals, concepts, behavior, interface semantics, configuration, tradeoffs, acceptance, and open questions |
| Leaf `实现.md` | Implementation proposals, actual delivery, code/build entry points, tests, and performance evidence |
| Research Overview | Highest research authority; subsequent constraints, formalization, and protocols must be discussed on this basis, without precedence for the old contract |

- Each rule has one owning document. Other documents summarize and link to it. Ownership follows responsibility, not whichever file is higher-level, newer, or more strongly worded.
- World documents define general capabilities and mechanisms; gameplay and save-continuation policies belong to the game platform, experiment locking and research validation to the research platform, and cognition implementation to agents.
- Record differences between the Research Overview and engineering scope, and unresolved mappings, in the research platform. Do not rewrite the overview to hide a conflict.
- Keep the module hierarchy: parent overviews, paired leaf design/implementation files. Unstarted topics can remain in the parent inventory; do not mass-create empty directories.

### Separate Status Dimensions

- **Design status**: `待讨论` (topics only), `讨论中` (some rules settled), `基础确定` (responsibility and core behavior available to depend on), `可实施` (interfaces, edge cases, and acceptance sufficient for the stated scope). Paused discussion is an annotation, not completion.
- **Decision status**: `已确认` (confirmed requirement), `暂定` (provisional basis with an explicit review condition), `候选` (comparison only). Collect open decisions separately.
- **Implementation status**: `未实施` (not implemented), `部分实施` (partial), `已实现` (implemented). State verification scope and evidence.
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
- Documentation-only changes check whitespace diffs, local links and anchors, navigation, and status consistency. Formatting checks do not establish semantic correctness; avoid unrelated code tests for documentation changes.

## Testing

The current automated suite covers only the headless C++17 declaration engine in `kheker/native/`, using CMake 3.20 and CTest without Qt. After changes, run from the repository root:

```bash
cmake -S kheker/native -B build/work/native -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON
cmake --build build/work/native --config Debug --parallel 4
ctest --test-dir build/work/native --build-config Debug --output-on-failure
```

Tests live in `testbench/native/`; build outputs go under the ignored `build/work/` directory. [Native Engine CI](.github/workflows/native_engine.yml) builds the same suite on Linux, Windows, and macOS. Local verification results are recorded in the [implementation document](docs/研究平台/实验环境与声明接入/实现.md#5-验证与性能).

## Commit Conventions

Follow the [Conventional Commits](https://www.conventionalcommits.org/) spec and write your commit descriptions in Chinese.

## Contribution License

- Before opening a Pull Request, please read and agree to [CLA.md](CLA.md) (Contributor License Agreement) — it authorizes the maintainer to commercially license and distribute software incorporating your contributions
- Checking the "Contributor confirmation" box in the PR template counts as agreement to the CLA
- CI verifies this automatically via [CLA Check](.github/workflows/cla.yml); PRs without agreement are flagged as not mergeable
- If your contribution includes third-party material (code, libraries, assets, etc.), please note its source and applicable license in the PR
- The project is licensed under [CC BY-NC-SA 4.0](LICENSE); your contributions are also publicly released under that license

## Build and Release

Product packaging, versioning, and release processes have not been decided. Establish them after the corresponding design is confirmed.

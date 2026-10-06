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
| Parent `综述.md` | Responsibilities, child navigation, dependencies, milestone summaries, and cross-module blockers; link to leaf rules and delivery details |
| Leaf `设计.md` | Goals, concepts, behavior, interface semantics, configuration, tradeoffs, acceptance, and open questions |
| Leaf `实现.md` | Current engineering approach, code structure, delivery, limitations, performance measurements, and build entry points; links to the test document |
| Leaf `测试.md` | Key guarantees mapped to test entry points, validation purposes, commands, and coverage limits; no per-run results; create when automated test cases exist |
| Research Overview | Highest research authority; subsequent constraints, formalization, and protocols must be discussed on this basis, without precedence for the old contract |

- Each rule has one owning document. Other documents summarize and link to it. Ownership follows responsibility, not whichever file is higher-level, newer, or more strongly worded.
- README covers positioning, usable milestones, and quick entry points. Parent overviews do not repeat feature inventories, test counts, or implementation history. Update summaries at milestones; the owning implementation document maintains detailed delivery status.
- World documents define general capabilities and mechanisms; gameplay and save-continuation policies belong to the game platform, experiment locking and research validation to the research platform, and cognition implementation to agents.
- Record differences between the Research Overview and engineering scope, and unresolved mappings, in the research platform. Do not rewrite the overview to hide a conflict.
- Keep the module hierarchy: parent overviews, paired leaf design/implementation files, and test documents when automated test cases exist. Unstarted topics can remain in the parent inventory; do not mass-create empty directories or test documents.

### Separate Status Dimensions

- **Design status**: `待讨论` (topics only), `讨论中` (some rules settled), `基础确定` (responsibility and core behavior available to depend on), `可实施` (interfaces, edge cases, and acceptance sufficient for the stated scope). Paused discussion is an annotation, not completion.
- **Decision status**: `已确认` (confirmed requirement), `暂定` (provisional basis with an explicit review condition), `候选` (comparison only). Collect open decisions separately.
- **Implementation status**: `未实施` (not implemented), `部分实施` (partial), `已实现` (implemented). State verification scope and evidence.
- A commit, move, formatting pass, change of discussion topic, or lack of objection does not approve a candidate. Editors must not promote recommendations to confirmed rules without an explicit design decision.

Headers briefly state status, scope, and navigation; keep detailed scope in the body rather than duplicating feature inventories. Design documents own decision maturity, implementation documents own delivery status and verification scope, and parent overviews retain consistent milestone summaries only.

### Leaf Templates

Organize `设计.md` around the following topics, combining sections where useful. Link to contracts and failure semantics already defined in confirmed rules rather than repeating them. Keep unstarted documents brief rather than inventing content to fill a template:

1. **Goals and rationale**: problem, use cases, and neighboring responsibilities.
2. **Goals and acceptance**: goals, checkable criteria, and related rules; label candidate checks separately. Link delivery status to the implementation document and validation entry points to the test document.
3. **Concepts and terminology**: objects, state, and definitions.
4. **Confirmed rules**: define each rule once by topic; label provisional decisions and their review conditions separately.
5. **External contracts**: inputs, outputs, preconditions, effects, effective timing, and failure behavior; semantics before signatures.
6. **Boundaries and failure behavior**: units, ranges, defaults, mutability, and applicable exceptional cases.
7. **Tradeoffs and candidates**: reasons, costs, alternatives, and undecided parts.
8. **Open questions**: impact, whether implementation is blocked, and the responsible module or phase.

Order `实现.md` as: **Design basis → Code structure and engineering approach → Current scope and limitations → Build/run entry points**, with links to test documents; record performance conclusions and measurement conditions here. Focus on component responsibilities, data flow, and source entry points; link behavior contracts to design and complete signatures and field inventories to code. Unimplemented modules may use only design basis and current status; keep candidates in design documents and never invent code paths or commands.

Order `测试.md` as: **Purpose and scope → Commands and verification environments → Guarantee-to-test index**. Group by behavior and explain key conditions, expectations, purposes, and test entry points. For complex scenarios, include independent reference-value sources and coverage limits. Test code owns exact inputs, steps, and assertions; do not require a prose translation of every case or assertion. Test registration owns the complete inventory and counts; actual runs and CI determine pass status. State shared commands and environments once.

### Quality and Maintenance Workflow

- Important rules may have stable module-local IDs for contracts and acceptance references; not every paragraph needs one. Mark illustrative values so they cannot be mistaken for defaults.
- Use prose for purpose and tradeoffs, and tables or lists for parallel cases and behaviors. Simplification must preserve interface preconditions and failure semantics. Keep rule headings unique and link goal summaries to rules so duplicate headings do not redirect anchors to the wrong section.
- Replace vague goals such as "deterministic" or "fast" with scoped behavior or measurement targets. Ordinary engineering acceptance does not automatically require research proofs.
- Record candidates during discussion and update the owning rule when confirmed. At milestones retain the final structure, constraints, and relevant rationale, removing completed migration steps, old-structure comparisons, and conversational corrections. Git/issues preserve ordinary history; use separate decision records only when justified.
- Review the owning design, implementation, and test documents on changes, updating only affected semantics, delivery scope, or coverage. Internal refactoring usually only updates implementation entry points. Review consumers for cross-module contract changes, update parent summaries at milestones, repair links/navigation on moves, and maintain bilingual entry points together.
- Documentation-only changes check whitespace diffs, local links and anchors, navigation, and status consistency. Formatting checks do not establish semantic correctness; avoid unrelated code tests for documentation changes.

## Testing

The current automated suite covers three parts: the headless C++17 declaration engine in `kheker/engine/`, the world library's space slice in `olam/`, and the workbench in `kheker/workbench/` (the headless session library needs no Qt; the Qt GUI requires Qt 6.4 or newer). All use CMake 3.20 and CTest. After changes, run from the repository root:

```bash
cmake -S kheker/engine -B build/engine -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON
cmake --build build/engine --config Debug --parallel 4
ctest --test-dir build/engine --build-config Debug --output-on-failure
```

```bash
cmake -S olam -B build/olam -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON
cmake --build build/olam --config Debug --parallel 4
ctest --test-dir build/olam --build-config Debug --output-on-failure
```

```bash
cmake -S kheker/workbench -B build/workbench -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON
cmake --build build/workbench --config Debug --parallel 4
ctest --test-dir build/workbench --build-config Debug --output-on-failure
```

Add `-DASCEND_WORKBENCH_BUILD_GUI=OFF` to the workbench configure command to build only the headless parts. Tests live in `testbench/engine/`, `testbench/olam/`, and `testbench/workbench/`; build outputs go under the ignored `build/` directory. [Engine CI](.github/workflows/engine.yml), [Olam World CI](.github/workflows/olam_world.yml), and [Workbench CI](.github/workflows/workbench.yml) build the corresponding suites on Linux and Windows; macOS is not included yet. Test cases and coverage limits are recorded in the [core test document](docs/研究平台/实验环境与声明接入/测试.md), the [space test document](docs/世界/世界框架/空间系统/测试.md), and the [workbench test document](docs/研究平台/因果建模工作台/测试.md).

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

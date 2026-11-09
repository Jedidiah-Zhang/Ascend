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
| Research Overview | Highest research authority; subsequent constraints, formalization, and protocols must be discussed on this basis |

- Each rule has one owning document. Other documents summarize and link to it. Ownership follows responsibility, not whichever file is higher-level, newer, or more strongly worded.
- `AGENTS.md` contains task reading paths, essential operational constraints, and verification links, not feature inventories or build commands. Read the owning module first and follow dependencies as needed; do not maintain a duplicate AI knowledge base.
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

### Leaf Organization

Organize `设计.md` by topic; no fixed section count or empty template sections are required:

1. **Goals and scope**: the problem, neighboring responsibilities, necessary terminology, and acceptance criteria.
2. **Current rules**: keep a topic's preconditions, inputs/outputs, timing, boundaries, failure behavior, and necessary rationale together. Do not repeat a contract in separate rule, interface, and boundary sections. Preserve units, ranges, defaults, and exceptions; state review conditions for provisional decisions.
3. **Active open questions**: state only the unresolved scope, what it blocks, and who owns it; keep candidates separate from current rules.

Link delivery status to implementation and verification entry points to tests. Split long documents by independent responsibility, not arbitrary line counts. Use specific, stable headings and link directly to the owning rule.

Order `实现.md` as: **Design basis → Code structure and engineering approach → Current scope and limitations → Build/run entry points**, with links to test documents; record performance conclusions and measurement conditions here. Focus on component responsibilities, data flow, and source entry points; link behavior contracts to design and complete signatures and field inventories to code. Unimplemented modules may use only design basis and current status; keep candidates in design documents and never invent code paths or commands.

Order `测试.md` as: **Purpose and scope → Commands and verification environments → Guarantee-to-test index**. Group by behavior and explain key conditions, expectations, purposes, and test entry points. For complex scenarios, include independent reference-value sources and coverage limits. Test code owns exact inputs, steps, and assertions; do not require a prose translation of every case or assertion. Test registration owns the complete inventory and counts; actual runs and CI determine pass status. State shared commands and environments once.

### Quality and Maintenance Workflow

- Important rules may have stable module-local IDs for contracts and acceptance references; not every paragraph needs one. Mark illustrative values so they cannot be mistaken for defaults.
- Use prose for purpose and tradeoffs, and tables or lists for parallel cases and behaviors. Simplification must preserve interface preconditions and failure semantics. Keep rule headings unique and link goal summaries to rules so duplicate headings do not redirect anchors to the wrong section.
- Replace vague goals such as "deterministic" or "fast" with scoped behavior or measurement targets. Ordinary engineering acceptance does not automatically require research proofs.
- Current documents exclude superseded designs, rejected candidates, completed migrations, and discussion transcripts; do not move them into a history appendix on the default reading path. Git/issues preserve history. Retain only necessary rationale for decisions likely to be inadvertently reversed.
- Keep small open questions in the owning design; use on-demand discussion drafts or issues for complex comparisons, explicitly marked as non-requirements. Once confirmed, update the single source, remove obsolete candidates, close resolved questions, and remove discussion drafts. Keep confirmed but unimplemented designs. Record specific design/code conflicts rather than silently replacing design with implementation.
- Review the owning design, implementation, and test documents on changes, updating only affected semantics, delivery scope, or coverage. Internal refactoring usually only updates implementation entry points. Review consumers for cross-module contract changes, update parent summaries at milestones, repair links/navigation on moves, and maintain bilingual entry points together.
- Documentation-only changes check whitespace diffs, local links and anchors, navigation, and status consistency. Formatting checks do not establish semantic correctness; avoid unrelated code tests for documentation changes.

## Testing

After changes, configure, build, and run CTest from the repository root using the owning module's entry below. Its implementation document owns commands and build options. All three use C++17 and CMake 3.20; the Qt UI additionally requires Qt 6.4 or newer with Widgets and Test.

| Module | Build commands and options | Verification scope |
| --- | --- | --- |
| `kheker/engine/` | [Engine build](docs/研究平台/实验环境与声明接入/实现.md#4-构建与运行入口) | [Engine tests](docs/研究平台/实验环境与声明接入/测试.md) |
| `olam/` | [World-library build](docs/世界/世界框架/空间系统/实现.md#4-构建与运行入口) | [Space and module integration](docs/世界/世界框架/空间系统/测试.md) · [Time](docs/世界/世界框架/时间系统/测试.md) · [Terrain](docs/世界/世界框架/地形与水文/测试.md) |
| `kheker/workbench/` | [Workbench build](docs/研究平台/因果建模工作台/实现.md#4-构建与运行入口) | [Session, state layer, and UI](docs/研究平台/因果建模工作台/测试.md) |

Tests live in corresponding `testbench/` subdirectories; build outputs go under the ignored `build/` directory. Platform matrices are maintained in [Engine CI](.github/workflows/engine.yml), [Olam World CI](.github/workflows/olam_world.yml), and [Workbench CI](.github/workflows/workbench.yml). CI configuration is not evidence of an actual passing run.

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

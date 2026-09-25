# Ascend

> Construct an interactive world with complete causal ground truth, and evaluate agents' cognition and behavior against its true generative mechanisms.

[中文](README.md) | English

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.14-3776AB?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/C-99-A8B9CC?logo=c&logoColor=white" alt="C">
  <img src="https://img.shields.io/badge/Godot-4.x-478CBF?logo=godotengine&logoColor=white" alt="Godot">
  <img src="https://img.shields.io/badge/SQLite-3-003B57?logo=sqlite&logoColor=white" alt="SQLite">
  <img src="https://img.shields.io/badge/JSON-over%20TCP-000000?logo=json&logoColor=white" alt="JSON over TCP">
  <img src="https://img.shields.io/badge/Lean-4.34%20%2B%20Mathlib-000000" alt="Lean + Mathlib">
  <img src="https://img.shields.io/badge/License-CC%20BY--NC--SA%204.0-EF9421" alt="License">
</p>

## Introduction

**Ascend** is an AI-native world simulation platform aimed at both research and games. The world is not a static backdrop for NPCs, but a continuously evolving system: its complete state, intra-frame update order, structural equations, exogenous random sources, and legal interventions are all explicitly declared by the researcher, constituting the causal ground truth; agents can only come to know it gradually through restricted perception and action.

The core idea:

> Construct an interactive world with complete causal ground truth, in which initial states, exogenous randomness, interventions, and observation processes can all be explicitly declared, and evaluate agents' world models against the outcomes produced by the true generative mechanisms.

## Highlights

- **Executable causal ground truth** — the world's complete state, structural equations, random sources, and legal interventions are all explicitly declared; engine trajectories are traceable node by node, with implementation checked against declaration.
- **Unit-level counterfactuals** — the same random experimental unit can produce strictly paired intervention/baseline parallel trajectories (CRN), separating intervention effects from two independent random fluctuations.
- **Falsifiable agent evaluation** — judged by operational causal capability: pre-registered query families, held-out test interventions, and strict scoring rules; no claims that cannot be refuted by experiment.
- **One source for research and play** — when using the official world, research and gameplay share its declared mechanisms. The new architecture also allows researchers to declare other causal systems and replace the experimental environment entirely.

The research questions fall into three progressive phases:

- **Phase 1 · Executable Causal Ground Truth (under construction)** — turn the world declaration into a uniquely executable, traceable, and intervenable dynamic structural causal system, and verify that the engine implementation agrees with the declaration.
- **Phase 2 · Identification Boundaries and Single-Agent Capability (not started)** — whether an embodied agent can, within a pre-registered history distribution, intervention range, and prediction window, make correct probabilistic predictions of the consequences of interventions it has not seen.
- **Phase 3 · Multi-Agent Causality and Macrostructure (not started)** — whether individuals can distinguish physical consequences, others' responses, and the influence of joint policies, and whether local interactions can produce stable macrostructures.

The author-written [Research Overview](docs/研究理论/研究综述.md) is the highest guiding authority for the research motivation, conceptual framework, and three progressive research directions. Specific research constraints and validation plans will be reconsidered for the new architecture.

The currently implemented game features are limited to world generation, time and weather progression, event recording, save rollback, and a debug terminal; NPCs, collective society, and player gameplay are still in the design phase. A complete rewrite is being developed around two libraries and two platforms. Space and time foundations are designed but not yet implemented. The research platform now has an initial headless C++ module registration, binding, and invocation slice; its future workbench will use Qt. See the documentation links below.

The causal-ground-truth and strict-reproducibility goals above apply to the corresponding research configurations. The new architecture permits community game mods with new algorithms that do not satisfy research protocols. The research platform checks applicability, while the game prioritizes playability and compatible save continuation.

The new [research platform](docs/研究平台/综述.md#自定义因果实验环境) accepts causal systems declared under the research rules as experimental environments, without requiring a game world, spatial geometry, entities, or a game clock. The official world is one implementation; generic environment declarations and integration interfaces are still being designed.

## Design Philosophy

- **The world precedes agents** — the causal world exists independently of any agent's knowledge; agents can only come to know it gradually through their own observations and actions, and their internal representations are not the world's true state.
- **Truth is declared, not discovered after the fact** — the world's generative mechanisms are explicitly declared as the causal ground truth; event records and research logs serve only tracing and verification, and are not the causal mechanisms themselves.
- **Reproducibility is infrastructure** — world generation, stochastic processes, and intervention execution all have deterministic replay mechanisms, so the same random experimental unit can produce strictly paired parallel trajectories.

Engineering-level development principles (deep modules and low coupling, no patch-style code, frontend/backend separation, etc.) are in [CONTRIBUTING.md](CONTRIBUTING.md).

## Package Structure

The current implementation's core packages use lowercase ASCII transliterations of Hebrew names; this layout does not constrain the new architecture:

| Package | Hebrew and meaning | Responsibility |
| --- | --- | --- |
| `olam` | עולם · world | World declarations, compilation, simulation runtime, and causal protocols |
| `miskhak` | משחק · game | Game wrapper, server, and Godot client (`miskhak/client/`) |
| `nefesh` | נפש · life, self, and psyche | Agent cognition for Phase 2 (reserved) |
| `kheker` | חקר · inquiry | Research probes, declaration verification, acceptance checks, and Lean proofs |

`testbench/` contains the test suites. `testbench/native/` tests the new C++ core in `kheker/native/`; the other existing backend tests use Python.

## Getting Started

Requirements: Python 3.14, Godot 4.x.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python miskhak/run_server.py   # run from the repo root (localhost by default)
```

Open `miskhak/client/` with Godot to run the game. Packaging and release: see [build/README.md](build/README.md).

The headless C++ engine builds independently with CMake 3.20 and a C++17 compiler. Its example and tests do not require Qt. Build commands and the current verification scope are in the [engine implementation document](docs/研究平台/实验环境与声明接入/实现.md#4-代码与构建入口).

## Documentation

- [Research Overview](docs/研究理论/研究综述.md) — the author-written highest research authority, preserved unchanged
- [Research Documentation](docs/研究方案与理论.md) — current authority, scope for renewed discussion, and historical navigation
- [Archived Research](docs/归档/研究理论/README.md) — former contracts, formalization, protocols, and implementation notes, for historical reference
- [Overall Architecture](docs/整体架构.md) — language and library boundaries, dependency rules, packaging, and interface principles
- [World](docs/世界/综述.md) — space and time foundations settled; state and mechanism interfaces discussed module by module
- [Agents](docs/智能体/综述.md) — agent implementation, cognition, and decisions
- [Game Platform](docs/游戏平台/综述.md) — gameplay, sessions, and presentation
- [Research Platform](docs/研究平台/综述.md) — declaration engine, causal modeling workbench, and experiment development plan
- [Archived Design](docs/归档/README.md) — former game and engineering designs and diagrams, for historical reference

Reading order: overall architecture → module overview → leaf design → implementation and verification. Design and implementation status are separate; candidates are not implementation requirements. See [Documentation Maintenance](CONTRIBUTING.en.md#documentation-maintenance) for writing and maintenance conventions.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) (design principles, development workflow, testing and commit conventions). Please read [CLA.md](CLA.md) before submitting a PR.

## License

Licensed under the [CC BY-NC-SA 4.0](LICENSE) license. Commercial use requires contacting the author.

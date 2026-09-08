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
- **One source for research and play** — players and agents live in the same declared world; research conclusions and gameplay design share the same world mechanisms.

The research questions fall into three progressive phases:

- **Phase 1 · Executable Causal Ground Truth (under construction)** — turn the world declaration into a uniquely executable, traceable, and intervenable dynamic structural causal system, and verify that the engine implementation agrees with the declaration.
- **Phase 2 · Identification Boundaries and Single-Agent Capability (not started)** — whether an embodied agent can, within a pre-registered history distribution, intervention range, and prediction window, make correct probabilistic predictions of the consequences of interventions it has not seen.
- **Phase 3 · Multi-Agent Causality and Macrostructure (not started)** — whether individuals can distinguish physical consequences, others' responses, and the influence of joint policies, and whether local interactions can produce stable macrostructures.

See the [Research Overview](docs/研究理论/研究综述.md) for the full motivation, unified formal system, four research protocols, and phase plan.

The currently implemented game features are limited to world generation, time and weather progression, event recording, save rollback, and a debug terminal; NPCs, collective society, and player gameplay are still in the design phase. The game vision is described in [Game Overview and Worldview](docs/游戏综述与世界观.md).

## Design Philosophy

- **The world precedes agents** — the causal world exists independently of any agent's knowledge; agents can only come to know it gradually through their own observations and actions, and their internal representations are not the world's true state.
- **Truth is declared, not discovered after the fact** — the world's generative mechanisms are explicitly declared as the causal ground truth; event records and research logs serve only tracing and verification, and are not the causal mechanisms themselves.
- **Reproducibility is infrastructure** — world generation, stochastic processes, and intervention execution all have deterministic replay mechanisms, so the same random experimental unit can produce strictly paired parallel trajectories.

Engineering-level development principles (deep modules and low coupling, no patch-style code, frontend/backend separation, etc.) are in [CONTRIBUTING.md](CONTRIBUTING.md).

## Getting Started

Requirements: Python 3.14, Godot 4.x.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cd backend && ../.venv/bin/python run_server.py   # start the backend (localhost by default)
```

Open `frontend/` with Godot to run the game. Packaging and release: see [build/README.md](build/README.md).

## Documentation

- [Research Overview](docs/研究理论/研究综述.md) — the sole master document for research goals, concept definitions, and notation
- [Phase 1 Implementation Definition](docs/研究理论/第一阶段实施定义.md) — the minimal contract for world declaration and engine implementation
- [Game Overview and Worldview](docs/游戏综述与世界观.md)
- [Design Documents](docs/) — organized by module: world framework, living individuals, mind system, gene system, collective society, player actions, presentation layer

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) (design principles, development workflow, testing and commit conventions). Please read [CLA.md](CLA.md) before submitting a PR.

## License


Licensed under the [CC BY-NC-SA 4.0](LICENSE) license. Commercial use requires contacting the author.

# Ascend

> An AI-native simulation platform that builds verifiable causal worlds and studies how agents learn, interact, and evolve within them.

[中文](README.md) | English

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.14-3776AB?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/C-99-A8B9CC?logo=c&logoColor=white" alt="C">
  <img src="https://img.shields.io/badge/Godot-4.x-478CBF?logo=godotengine&logoColor=white" alt="Godot">
  <img src="https://img.shields.io/badge/SQLite-3-003B57?logo=sqlite&logoColor=white" alt="SQLite">
  <img src="https://img.shields.io/badge/JSON-over%20TCP-000000?logo=json&logoColor=white" alt="JSON over TCP">
  <img src="https://img.shields.io/badge/License-CC%20BY--NC--SA%204.0-EF9421" alt="License">
</p>

---

## Positioning

**Ascend** is an AI-native world simulation platform aimed at both research and games.

Its core idea:

> **First build a causal world that runs on its own, is reproducible, intervenable, and has an explicit Ground Truth; then study how agents form cognition, learn, and behave in it through their own experience.**

Ascend does not treat the world as a static backdrop for NPCs, but as a continuously evolving system.

---

## Research Motivation

Much current research on AI agents, world models, and NPCs shares a common problem:

> **The world an agent inhabits is usually just a dataset, an environment interface, or an unobservable black box—not a complete world with an explicit Ground Truth causal mechanism that keeps evolving and accepts intervention.**

This makes many questions hard to answer rigorously:

- What exactly has the agent learned?
- How does its world representation differ from the real world?
- Under what conditions can the causal structure be recovered?
- How does sample complexity scale with world complexity?
- How much error do observation noise, temporal dependence, and insufficient intervention coverage cause?
- How does a wrong causal understanding affect subsequent behavior?
- What collective phenomena emerge when multiple agents with different cognition interact?

Ascend aims to first solve the most fundamental part:

> **Construct a dynamic causal world that can be rigorously analyzed and verified.**

On top of that, it will gradually introduce learning agents and social systems.

---

## Game Goal

The player lives in an independently evolving world. Through exploration, observation, communication, building, trading, management, genetic editing, and social interaction, the player can alter the trajectory of the world and of individual development.

---

## Design Philosophy

### World Before Agent

The world exists prior to any agent.

Terrain, climate, hydrology, ecology, the passage of time, and other systems evolve continuously according to their own mechanisms, rather than being generated on the fly for some NPC's behavior. NPCs and the player can only come to know the world gradually through observation, action, and interaction—and NPCs must have no prior knowledge beyond the world. Hence:

$$
Ground\ Truth \neq Agent\ Belief
$$

The world holds a true state, while each agent holds its own internal representation.

### The World Is an Executable Causal System

The world is not merely a collection of data, but a programmatic system that keeps running.
The world state $S_t$ evolves under the world's mechanisms and external intervention:

$$
S_{t+1}=F(S_t, A_t, U_t, \epsilon_t)
$$

where:

- $S_t$: world state
- $A_t$: agent and player actions
- $U_t$: external intervention
- $\epsilon_t$: stochastic factors

The generative mechanisms inside the world constitute the Ground Truth causal system. WorldTree then records the events, state changes, and causal dependencies within it in a traceable manner.
The world can therefore be forward-simulated, intervened upon, and reproduced, serving as an experimental environment for causal research.

### Reproducibility

Reproducibility is treated as infrastructure: world generation, stochastic processes, event evolution, and future agent behavior should all have deterministic replay mechanisms.
This lets researchers reproduce experiments, compare models, replay events, and apply different interventions to the same event.

---

## System Architecture

> Currently an early-stage blueprint; agents and social systems are still in the design phase.

### Tech Stack

- **Godot 4.x**: rendering, UI, input, audio
- **Python backend** (`backend/`): all core logic
- **Communication**: JSON over TCP (planned migration to MessagePack)
- Dependencies in `requirements.txt`

### Architectural Layers

The system is planned as three interconnected layers:

![Architecture layers](docs/diagrams/ascend-系统架构.层级图.svg)

**① World Layer** — A programmatically generated, continuously evolving, verifiable causal world, including terrain, climate, hydrology, ecology, resources, time, and event systems. The world runs independently of agents and provides observations to agents through a restricted perception interface. World-state changes are recorded by the event system and WorldTree for debugging, reproduction, and causal research.

**② Agent Layer** — Agents form their own internal state by perceiving the world, taking actions, and receiving feedback. Their internals may include: perception, memory, beliefs, needs, goals, learning, decision-making. The concrete model architecture will be determined during Phase 2 research.

**③ Interaction Layer** — Agents change the world through actions, and obtain new information through interaction with other agents and the player.

---

## Execution Phases

### Phase 1: A Verifiable Causal World (Under Construction)

The current primary goal is not NPCs, but completing the world foundation and verifying that WorldTree and the causal system as a whole are sufficient to support rigorous causal research—guaranteeing their correctness and interpretability.
This phase ultimately aims to answer:

> **Under what conditions can a dynamic causal world with known Ground Truth be correctly identified?**

and:

> **When a causal model fails, how does the degree of failure vary with sample size, structural complexity, noise, temporal dependence, and intervention coverage?**

#### Phase 1-A: Build the Ground Truth World

First build a world that runs independently, including terrain, elevation, temperature, rainfall, hydrology, climate, ecology, time, weather, resources, and so on.
These systems ensure the world has explicit, executable, traceable generative mechanisms, while also making the generated world look "plausible" enough to satisfy game design needs.

#### Phase 1-B: WorldTree

Use **WorldTree** to record events and causal structure within the world.
WorldTree is designed to satisfy the traceability, verifiability, reproducibility, temporal consistency, and intervention-localization needs of research.

A distinction must be made: **the causal relations that exist in the world's own mechanisms** versus **the relations recorded by the logging system to track events**. The two are not fully equivalent. WorldTree is a computable, traceable mapping of the Ground Truth causal mechanism, not the causal relations themselves.

#### Phase 1-C: Causal Theory Verification

Before any agents are introduced, verify the causal system itself with controlled experiments.
Research focuses include: assessing sample complexity, error propagation and counterfactuals, Granger equivalence, intervention sampling coverage, and comparing experimental results against theory.

---

### Phase 2: Agent Construction (Not Started)

Once the Ground Truth World and the causal verification system are stable, introduce agents.
The research question becomes:

> **How does an agent that does not know the world's Ground Truth form an internal representation of the world through its own experience?**

### Agents Do Not Receive Ground Truth Directly

Phase 1 yields the Ground Truth causal structure $G^*$ defined by the world's generative mechanisms, but agents cannot access it directly. The information available to an agent comes from its own observations $o_1,o_2,\dots,o_t$ and its own actions $a_1,a_2,\dots,a_t$.
Therefore, even agents in the same world may form different world representations because their perception, initial conditions, experience, and individual traits differ.
In Phase 2, the specific cognition and decision architecture itself is an object of study, not a predetermined engineering implementation.

### Individual Differences and Genes

The player's genetic editing is not for changing traditional numeric attributes, but for changing the initial conditions of an individual's interaction with the world.
The long-term goal is to study:

$$
Genome \rightarrow Perception + Needs + Learning + Behavior
$$

Individuals with different genomes may have different perception, physiological needs, preferences, learning ability, memory traits, and behavioral tendencies. Individuals with different genomes experience different events in the same world and thus form different internal representations.

---

### Phase 3: Intelligent Interaction (Not Started)

Once multiple agents coexist, research moves to the social level.
Each agent has its own $Belief_i$, $Memory_i$, $Goal_i$, $Experience_i$, and they influence one another through action and communication,
giving rise to information propagation, misbelief propagation, trust relations, cooperation and conflict, collective behavior, social norms, and cultural or knowledge transmission.

These phenomena should not depend primarily on preset rules for specific social phenomena, but should, as much as possible, emerge from cognitive, learning, and interaction mechanisms at the individual level.

#### Natural Language Communication

There is no plan to make a large language model the "soul" of an NPC.
An NPC's cognition, memory, beliefs, and behavior should come from its own internal model and experience.

Natural language can serve as the interaction interface between humans and agents. Ideally, the language model handles open-ended natural language without directly deciding the NPC's worldview and behavior.

Therefore, an NPC should be able to run independently even without a natural language interface.

---

### Directory Structure

```
backend/   Python backend (core logic)
frontend/  Godot frontend
build/     Build and packaging
data/      World content data (JSON: terrain/biome/climate/weather/world-gen parameters)
docs/      Design documents
lang/      Multilingual resources
research/  Research probe experiment scripts (causal theory verification)
requirements.txt  Python dependencies
```

---

## Design Documents

Full design documents live in `docs/`, organized by module:

- [Game Overview and Worldview](docs/游戏综述与世界观.md)
- [Research Plan and Theory](docs/研究方案与理论.md) — SCM, causal verification, sample complexity
- [Research Theory · Causal Theory Verification](docs/研究理论/因果理论验证/) — theorems, derivations, probe experiment criteria and results
- [World Framework](docs/世界框架/) — physics, time, ecology, event systems
- [Living Individuals](docs/生命个体/) — personality, physiology, body
- [Mind System](docs/心智系统/) — AI-native NPCs, goals, skills
- [Gene System](docs/基因系统/) — genetic operations, dietary compatibility
- [Collective Society](docs/群体社会/) — relationship graphs, world simulation, governance systems
- [Player Actions](docs/玩家行动/) — gameplay progression, building, economy
- [Presentation Layer](docs/表现层/) — visuals, interface, audio

---

## License

[CC BY-NC-SA 4.0](LICENSE) (commercial use requires contacting the author)

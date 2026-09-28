# Ascend

> Ascend explores whether artificial intelligence can learn how a simulated world works through interaction, with a long-term goal of a simulation game centered on genetic engineering and population evolution.

[中文](README.md) | English

## About the Project

Observing a correlation between events does not, by itself, tell us what would happen if one condition changed. Ascend plans to build simulations with explicit rules and controllable conditions. Researchers will be able to vary starting conditions, change the actions of AI programs that perceive and act in the environment (agents), and compare outcomes. This can help assess whether agents learn causal relationships rather than relying only on patterns in past observations.

The project's long-term goals also include a simulation game centered on genetic engineering and population evolution. The game and research tools are related but distinct: research can use the game's simulated world or an environment built specifically for an experiment.

## Project Structure and Research Stages

The planned architecture has two parallel tracks built on shared world-simulation capabilities:

```text
Planned shared world core
├── Research track (three progressive stages)
│   ├── 1. Build a world with explicit causal rules
│   ├── 2. Introduce a single agent into the world
│   └── 3. Study interactions among multiple agents
└── Game track: develop gameplay and a client around the shared core
```

These are parallel tracks, not consecutive project phases. Sharing the world core means sharing simulation capabilities, not using the same running world instance. Research may also use environments created specifically by researchers, rather than the game's world.

## Project status

Ascend is in the early stages of a rewrite. The core currently includes a developer-facing C++ prototype with no graphical interface. It can connect and run code-defined components and return their results. This prototype validates basic component interaction; it is not a complete simulation or an end-to-end research system.

The full world simulation, AI agents, research experiment management and evaluation tools, and a playable game client have not yet been implemented. The Godot client currently contains only a basic startup scene; gameplay is not yet available.

## Documentation

- [Research questions and theory](docs/研究理论/研究综述.md) — what the project aims to study and how its concepts are defined
- [Overall architecture](docs/整体架构.md) — planned components and their responsibilities
- [Research platform](docs/研究平台/综述.md) — research-tool design, current progress, and open questions
- [World simulation](docs/世界/综述.md) — the world library's role and design progress
- [AI agents](docs/智能体/综述.md) — agent responsibilities and design progress
- [Game direction](docs/游戏平台/综述.md) — gameplay, client, and mod plans

Most detailed design documents are currently available in Chinese.

## Build and test the C++ prototype

For developers: these commands build and run the automated tests of the C++ core and the world library's space slice; they do not launch a game or graphical interface. You need CMake 3.20 and a C++17-compatible compiler. Run them from the repository root:

```bash
cmake -S kheker/native -B build/work/native -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON
cmake --build build/work/native --config Debug --parallel 4
ctest --test-dir build/work/native --build-config Debug --output-on-failure
```

The world library's space slice builds independently:

```bash
cmake -S olam -B build/work/olam -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON
cmake --build build/work/olam --config Debug --parallel 4
ctest --test-dir build/work/olam --build-config Debug --output-on-failure
```

Build outputs go under `build/work/native/` and `build/work/olam/`. See the [core test notes](docs/研究平台/实验环境与声明接入/测试.md) and the [space test notes](docs/世界/世界框架/空间系统/测试.md) for current coverage and limitations.

## Contributing

See [CONTRIBUTING.en.md](CONTRIBUTING.en.md) for development and documentation conventions. Please read the [Contributor License Agreement](CLA.en.md) before submitting a pull request.

## License

Licensed under [CC BY-NC-SA 4.0](LICENSE). Commercial use requires contacting the author.

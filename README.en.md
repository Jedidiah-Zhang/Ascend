# Ascend

> An AI-native platform for causal world-model research and simulation games. The project is undergoing a complete rewrite.

[中文](README.md) | English

<p align="center">
  <img src="https://img.shields.io/badge/C%2B%2B-17-00599C?logo=cplusplus&logoColor=white" alt="C++17">
  <img src="https://img.shields.io/badge/Godot-4.x-478CBF?logo=godotengine&logoColor=white" alt="Godot 4.x">
  <img src="https://img.shields.io/badge/License-CC%20BY--NC--SA%204.0-EF9421" alt="License">
</p>

## Current Status

The new architecture is being built across the world library, agent library, game platform, and research platform. The current implementation is one headless C++17 research-platform slice: module registration, public value and method checks, typed bindings, and invocation. It does not constitute a complete research protocol, world library, or game platform.

The world library, agent library, research workbench, experiment organization, and playable Godot client are not implemented yet. `miskhak/client/` currently retains only the Godot project shell and bootstrap scene.

## Architecture and Research Basis

- [Overall Architecture](docs/整体架构.md) — responsibilities, dependencies, and open boundaries across the two libraries and platforms
- [World](docs/世界/综述.md) — world-library responsibilities and design progress
- [Agents](docs/智能体/综述.md) — agent responsibilities and design progress
- [Game Platform](docs/游戏平台/综述.md) — game assembly, sessions, and presentation
- [Research Platform](docs/研究平台/综述.md) — causal environments, declaration engine, and research plan
- [Research Overview](docs/研究理论/研究综述.md) — the author-written highest research authority

Design status and implementation status are tracked separately; candidates are not implementation requirements. See [CONTRIBUTING.en.md](CONTRIBUTING.en.md#documentation-maintenance) for documentation conventions.

## Build and Verify

The current C++ core requires CMake 3.20 and a C++17 compiler. Run from the repository root:

```bash
cmake -S kheker/native -B build/work/native -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON
cmake --build build/work/native --config Debug --parallel 4
ctest --test-dir build/work/native --build-config Debug --output-on-failure
```

Build outputs go under the ignored `build/work/native/` directory. See the [declaration-engine implementation document](docs/研究平台/实验环境与声明接入/实现.md#4-代码与构建入口) for the current verification scope and limitations.

## Contributing

See [CONTRIBUTING.en.md](CONTRIBUTING.en.md) for design principles, documentation, testing, and commit conventions. Please read [CLA.md](CLA.md) before submitting a PR.

## License

Licensed under [CC BY-NC-SA 4.0](LICENSE). Commercial use requires contacting the author.

# Contributing

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

## Testing

### Backend (Python / pytest)

After every code change, run the affected unit tests (no need to run the full suite during day-to-day development):

```bash
cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest --testmon -n 4 -q
```

- `-n 4` is a deliberately conservative degree of parallelism: with `-n auto`, workers are spawned to match the machine's core count, which can OOM/saturate the CPU during world generation. This command is for unit tests only
- Integration tests must run serially (port/subprocess conflicts): `cd backend && ../.venv/bin/python -m pytest tests/integration/ -v`
- No need to run the full suite locally; CI's `test` job runs it automatically before release

### Frontend (GDScript / GUT)

```bash
cd frontend && ./run_tests.sh unit
```

GUT is not distributed with the repo (`frontend/addons/gut` only needs to be installed locally), so frontend tests run only locally.

## Commit Conventions

Follow the [Conventional Commits](https://www.conventionalcommits.org/) spec and write your commit descriptions in Chinese.

## Contribution License

- Before opening a Pull Request, please read and agree to [CLA.md](CLA.md) (Contributor License Agreement) — it authorizes the maintainer to commercially license and distribute software incorporating your contributions
- Checking the "Contributor confirmation" box in the PR template counts as agreement to the CLA
- CI verifies this automatically via [CLA Check](.github/workflows/cla.yml); PRs without agreement are flagged as not mergeable
- If your contribution includes third-party material (code, libraries, assets, etc.), please note its source and applicable license in the PR
- The project is licensed under [CC BY-NC-SA 4.0](LICENSE); your contributions are also publicly released under that license

## Release

- Single source of version: `build/nuitka/version.txt` (derives release naming, artifact filenames, Windows exe properties, and main-menu display)
- Pushing a tag triggers CI auto-release — currently the backend (research platform) only; this is normally done by maintainers:

```bash
git push origin main
git tag v<version> && git push origin v<version>
```

- Local build: `bash build/build_release.sh all` (see `build/README.md`)
- Frontend distribution (including proprietary assets) follows a private process, not this repo's CI

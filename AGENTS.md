# Repository Guidelines

## Project Purpose & High-Level Overview

AGCL is a **Python-first hybrid control plane** for orchestrating **local compute**, **containers**, **Kubernetes**, **GCP**, **Azure**, **JSONBin**, and **parallel multi-agent toolkits** from one workstation. It targets **AI researchers**, **agent engineers**, and **infra-oriented builders** who want **credit-aware routing**, **multi-agent “orchestra”** semantics (many agents and toolkits **at once**), **recursive agent modelling**, **experimental training hooks**, and **latent-state-style persistence** for repeating tasks—without forcing a multi-tenant SaaS posture or replacing full cloud platforms wholesale.

## Project Structure & Module Organization

```
AGCL/
├── plan.md                 # Beam Phase 1 product intent (goals, non-goals, constraints)
├── pyproject.toml          # Package metadata; wheels install `src/agcl` (`uv run agcl`)
├── main.py                 # Primary CLI / FastAPI entry (serve, node, recursive, MCP, …)
├── agcl/                   # Core Python modules (orchestrator, toolkits, integrations, recursive, mini, …)
├── src/agcl/               # Packaged namespace used by setuptools console script `agcl`
├── tests/                  # Pytest suite (`pythonpath` includes `src` per pyproject)
├── docs/                   # Guides and integration notes; `docs/SPEC.md` is canonical architecture
├── collab_progress/        # Collaboration protocol, changelog index, dated progress notes
├── clients/npm/            # TypeScript client package (GUI/tooling consumers)
├── plugins/                # Example / extension plugins
├── scripts/                # Auxiliary validation or training scripts
└── mas.json / .env.example # Example configuration surfaces (extend cautiously)
```

Work **against `plan.md` and `docs/SPEC.md`** before expanding scope. If packaging layout (`agcl/` vs `src/agcl/`) diverges during development, prefer changes that keep **`uv run agcl`** and **`python main.py …`** behavior aligned and documented here.

## Build, Test, and Development Commands

- **`uv sync --extra dev`** — install project + dev deps (`pytest`) into the local environment.
- **`uv run agcl --help`** / **`uv run agcl --version`** — smoke-test the setuptools console entry (`src/agcl`).
- **`python main.py serve --port 8000`** — run the FastAPI surface defined in `main.py` (invoke from repo root so `agcl` imports resolve).
- **`uv run pytest`** — run automated tests under `tests/` using `[tool.pytest.ini_options]` (`pythonpath` includes `src`).

Use **official docs** for FastAPI, Uvicorn, Anthropic/OpenAI SDKs, and cloud SDKs when touching integrations.

## Coding Style & Naming Conventions

- **Python 3.13+**; prefer **type hints** on public APIs and non-trivial internals.
- Follow **PEP 8** naming (`snake_case` modules/functions, `PascalCase` types); keep CLI flags stable once documented.
- Introduce **formatters/linters** (for example **Ruff**) only when the repo adopts shared config—until then, match surrounding file style.

## Testing Guidelines

- **Framework:** **pytest**; tests live under **`tests/`**, named `test_*.py` or `*_test.py` per convention already configured.
- **New behavior:** add focused tests near the feature; prefer fast unit tests for orchestration and routing logic; integration tests only where they reduce real regressions (containers, cloud mocks).
- **Coverage:** when coverage tooling is wired in, treat raised thresholds as CI gates; until then, **don’t merge risky paths without tests** called out in `collab_progress`.

## Commit & Pull Request Guidelines

- Use **clear, imperative** commit subjects (`Add JSONBin session adapter`); body explains **why** when non-obvious.
- **Pull requests** should describe scope, link tracking issues when applicable, note **verification** (`pytest`, manual smoke commands), and flag **env/secrets** impacts for reviewers.

## Documentation & Knowledge Sources

ALWAYS reference official documentation for libraries/frameworks before implementing anything new or changing existing behavior.

### **Primary Sources**:

1. **[docs/SPEC.md](docs/SPEC.md)** for project scope, architecture, and non-goals before designing or implementing product behavior.
2. Official library and framework documentation on the internet. Use your built-in `web search` tools.
3. If all else fails, browse dependency source locally (for example through your package manager cache, a vendored directory, or published source tarballs and repository tags). Ask the user to add or update dependencies if needed so the relevant code is available to you.
4. **[plan.md](plan.md)** at the repository root for Beam Phase 1 intent—goals, non-goals, classification signals, and open questions—before expanding connectors, training surfaces, or MCP contracts.

## Collaboration & Progress Tracking Protocol

**Before starting any new task:**
- The `collab_progress` folder exists to track and understand the current state, recent changes, and overall repo progress.
- Decide whether reviewing these is necessary for the given task. Then `ls` the folder contents to view file names and check relevant files if you so decide.

**After completing any significant change:**
- Follow the instructions in `collab_progress/PROTOCOL.md` to document your work.

**This protocol is mandatory for all contributors and must be followed for every codebase change.**

## Full-Code Ownership & Test-Driven Development (TDD) Mandate

> **You are the sole implementor for this repository.**
>
> * Treat the project as a green-field codebase:
>   Write all source files, config, and docs needed to meet requirements.
> * Follow strict TDD:
>   - Write a failing automated test for any new behavior before writing production code, using this repository's standard test runner and layout.
>   - Write minimal production code to pass the test.
>   - Refactor for clarity after passing.
> * Maintain strong automated test coverage on new and changed behavior; add edge and corner-case tests for every feature, using the project's coverage tooling when it is configured.

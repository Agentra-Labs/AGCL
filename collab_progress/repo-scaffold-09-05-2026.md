# Repository scaffold (Beam Phase 2)

## Summary

Established the **single-package src layout** (`src/agcl/`), a minimal **CLI entry** (`agcl.cli:main`), **pytest** smoke test, **documentation placeholders** (`docs/`), **MIT LICENSE**, **Python `.gitignore`**, and the **`collab_progress/`** collaboration tracker per Beam scaffolding rules. Root **`plan.md`** records product intent; **`README.md`** follows the scaffold template for onboarding.

## Files Changed

- `pyproject.toml` — build backend (`setuptools`), `src/` package discovery, `[project.scripts]` → `agcl`, optional `dev` extra with `pytest`, pytest config.
- `src/agcl/__init__.py`, `src/agcl/cli.py` — package version and argparse scaffold.
- `tests/test_package.py` — version assertion.
- `.gitignore`, `LICENSE`, `README.md`, `docs/.gitkeep`
- `collab_progress/CHANGELOG.md`, `PROTOCOL.md`, `README.md`, this note.

## Rationale

Beam Phase 2 requires a **minimal runnable skeleton** without product logic so later phases (`AGENTS.md`, `docs/SPEC.md`) and feature work attach to a consistent layout.

## Verification

Planned commands (run after `uv sync --extra dev`):

- `uv run agcl --version` → prints `0.1.0`
- `uv run pytest` → passes

## Issues

- Heavy dependencies (`torch`, `llama-cpp-python`, etc.) may lengthen first install; CI or optional extras may be split in a future change.

## Next Steps

- Phase 3: author root **`AGENTS.md`** from `plan.md` and `agents_config.json`.
- Phase 4: author **`docs/SPEC.md`** from `plan.md` and optional web research.

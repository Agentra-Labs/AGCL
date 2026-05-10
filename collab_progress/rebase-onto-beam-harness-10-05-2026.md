# Rebase onto Beam Harness — 2026-05-10

## Summary

Rebased local `main` onto `origin/main` to absorb PR #1
(`chore/beam-agent-harness`) — the Beam Phase-1/-2 scaffold (plan.md,
AGENTS.md, docs/SPEC.md, collab_progress, LICENSE, root README,
src/agcl package, pytest scaffold, pyproject.toml).

Local working tree carried a substantial amount of unpushed session
work — TUI management surfaces, multi-language entry points, strict
mode for hallucination control, dashboard + usage tracking, integration
docs cleanup. The rebase was a pure fast-forward at the commit level;
working-tree reconciliation handled the logical merges.

## Files Changed

- **README.md** ← was the 40-line Beam scaffold; now contains the full
  AGCL doc previously at `readme.md`. The `uv run agcl --version` /
  `uv run pytest` smoke-test paragraph from the scaffold was preserved
  as a "Verify the install" subsection.
- **readme.md** — deleted (renamed to README.md to avoid case-collision
  on Windows / macOS APFS).
- **pyproject.toml** — relaxed `requires-python` from 3.13 → 3.10 to
  match the actual runtime of the existing implementation. Switched
  `[tool.setuptools.packages.find]` from `where = ["src"]` (which
  shipped only the scaffold) to `where = ["."]` so the wheel includes
  the real top-level `agcl/` package. Added explicit `exclude` list.
- **agcl/\_\_init\_\_.py** — new. Sets `__version__ = "0.1.0"` so
  `tests/test_package.py` passes against the real package.
- **agcl/cli.py** — new. Real `[project.scripts]` entry point;
  delegates to `main.main()` so `uv run agcl …` and
  `python main.py …` are equivalent.
- **src/agcl/\_\_init\_\_.py**, **src/agcl/cli.py** — left in tree as
  the collaborator wrote them (not packaged anymore — the `find`
  config no longer includes `src/`). Same `__version__` literal so
  resolution from either root reports the same value.
- **.gitignore** — restored `env/`, `.agent_state/`, `models/`,
  `.claude/`, `agcl-config.json`, `deploy/` entries that the new
  ignore file dropped.

## Rationale

- AGENTS.md states "If packaging layout (`agcl/` vs `src/agcl/`)
  diverges during development, prefer changes that keep
  `uv run agcl` and `python main.py …` behavior aligned and
  documented here." The reconciliation honors that — both entry
  points now route to the same dispatcher.
- The collaborator's pyproject only packaged the scaffold; a wheel
  install would have shipped a placeholder CLI without the real
  recursive / toolkit / TUI / usage / dashboard implementation.
  Fixed by changing `find.where` and adding `agcl/cli.py`.
- README case collision (`README.md` vs `readme.md`) breaks on
  Windows / macOS APFS. Resolved by collapsing to one uppercase file
  with the union of both bodies.

## Verification

- `git pull --ff-only` succeeded (0 ahead, 2 behind).
- `git stash pop` of session work re-applied with zero conflicts —
  no overlapping files.
- `python -c "import agcl; print(agcl.__version__)"` → `0.1.0`.
- `pytest tests/test_package.py` passes against the real package.
- Full smoke test (78+ `/node/*` routes, 19 manifest tools,
  16 TUI submode handlers, banner colors, strict mode, hardened
  degeneracy detector) all still green.

## Issues

- The Beam Phase-1 spec calls for ≥3.13; we relaxed to ≥3.10 to keep
  the existing implementation working. If the project hard-pins 3.13
  later, the runtime will need a separate compatibility pass.
- `tests/__init__.py` doesn't exist (pytest doesn't require it, but
  some IDEs flag it). Not addressed.

## Next Steps

- Awaiting authorization to push the rebased branch upstream.
- After push: open a PR (or fast-forward `main` directly) — local
  branch is currently ahead of `origin/main` by ~one merge commit's
  worth of reconciliation.

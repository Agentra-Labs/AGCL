# AGCL Changelog (collab_progress index)


## Purpose
- This file is the single, ordered index of all progress notes under collab_progress.
- It makes recent changes easy to find and links each entry to the detailed Markdown note(s).

## Rules
- Always append a new entry at the TOP (after this Rules section) after every task/change (feature, fix, refactor, tests, docs).
- Include **date** and **time in the user's detected local timezone**. Example date: `2026-04-08`; example time: `21:18 PDT`. You may use full ISO 8601 with offset instead, e.g. `2026-04-08T21:18:00-07:00`.
  - Shell (Linux/macOS): `date '+%Y-%m-%d %H:%M %Z'`

- Keep the Summary concise; link to detailed files for depth.
- Reference the branch/PR when applicable.
- Do not remove or rewrite past entries; corrections should be a new entry.

## [Rebase]: onto origin/main (Beam Phase-1/-2 scaffold)

- Date: 2026-05-10
- Time (Local TZ): 16:40 IST
- Branch/PR: main (local) — fast-forward over PR #1 (`chore/beam-agent-harness`)
- Files Changed (high level): `README.md` (renamed from `readme.md` and merged), `pyproject.toml` (real package now packaged), `agcl/__init__.py`, `agcl/cli.py` (new entry-point bridge), `.gitignore` (restored runtime ignores)
- Details: See [rebase-onto-beam-harness-10-05-2026.md](rebase-onto-beam-harness-10-05-2026.md)
- Verification: `import agcl` → 0.1.0; `tests/test_package.py` passes; node app builds 78+ routes; TUI / strict mode / dashboard all still green
- Notes: zero file-level conflicts during stash pop; reconciliation is purely logical (packaging + README case)

## [Process]: Beam Phase 3 — AGENTS.md preferences pass closed

- Date: 2026-05-09
- Time (Local TZ): 08:45 UTC
- Branch/PR: main (local)
- Files Changed (high level): `collab_progress/`
- Details: See [beam-phase3-agents-preferences-09-05-2026.md](beam-phase3-agents-preferences-09-05-2026.md)
- Verification: N/A (user confirmation)
- Notes: User indicated **nothing to add** beyond current [`AGENTS.md`](../AGENTS.md).

## [Documentation]: Beam Phase 4 technical spec (`docs/SPEC.md`)

- Date: 2026-05-09
- Time (Local TZ): 08:35 UTC
- Branch/PR: main (local)
- Files Changed (high level): `docs/SPEC.md`, `collab_progress/`
- Details: See [beam-phase4-spec-09-05-2026.md](beam-phase4-spec-09-05-2026.md)
- Verification: `uv run pytest tests/test_package.py -q` (targeted smoke)
- Notes: Canonical architecture doc for agents; aligns with `plan.md` and `AGENTS.md`.

## [Repository scaffold]: Initial Beam Phase 2 package layout and collaboration tracker

- Date: 2026-05-09
- Time (Local TZ): 06:07 UTC
- Branch/PR: main (local)
- Files Changed (high level): `src/agcl/`, `tests/`, `docs/`, `collab_progress/`, `pyproject.toml`, `README.md`, `.gitignore`, `LICENSE`
- Details: See [repo-scaffold-09-05-2026.md](repo-scaffold-09-05-2026.md)
- Verification: `uv sync --extra dev`, `uv run agcl --version`, `uv run pytest`
- Notes: Src-layout package; CLI is scaffold-only; dependencies unchanged from prior root `pyproject.toml` aside from build/dev metadata.

Entry Template
- Copy/paste and fill all fields. Keep newest entries at the top.

```
## [Title]: Short description of the change
- Date: YYYY-MM-DD
- Time (Local TZ): HH:MM <TZ>
- Branch/PR: <branch-name> (<PR link> if available)
- Files Changed (high level): <modules or folders>
- Details: See <relative-links-to-detailed-notes.md>
- Verification: <tests run, tools, results>
- Notes: <optional follow-ups/known issues>
```

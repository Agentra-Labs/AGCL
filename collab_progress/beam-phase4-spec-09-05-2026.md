# Beam Phase 4 — `docs/SPEC.md`

## Summary

Added **`docs/SPEC.md`**, the canonical technical specification for AGCL: **overview**, **goals & non-goals**, **architecture** (with Mermaid diagram and trade-offs), **repository mapping** (`main.py` / root `agcl/` vs `src/agcl` console entry), and **open questions** carried from [`plan.md`](../plan.md).

## Files Changed

- `docs/SPEC.md` (new)
- `collab_progress/CHANGELOG.md` (entry)
- `collab_progress/beam-phase4-spec-09-05-2026.md` (this note)

## Rationale

Beam Phase 4 closes the loop so **`AGENTS.md`** Primary Sources item **(1)** resolves to a real file; coding agents can read scope and non-goals before implementing connectors, training, or MCP surfaces.

## Verification

Run targeted smoke tests (fast):

```bash
uv run pytest tests/test_package.py -q
```

## Issues

- Full `pytest` over the entire repo may be slow or environment-dependent because of heavy optional ML dependencies; use targeted tests when iterating.

## Next Steps

- Resolve **open questions** in `docs/SPEC.md` as milestones land (JSONBin role, OAuth, packaging convergence).
- Optional: add CI that runs a **minimal** test subset on every push.

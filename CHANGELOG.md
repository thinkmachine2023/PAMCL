# PAMCL Changelog

All notable changes are documented here. This project follows semantic versioning for the `pamcl` package (current: 0.4.0).

## [0.4.0] — 2026-06 (refinements)

### Added / Improved
- Full symmetric CAUTION severity support for all constraint rule types:
  - `max`: `value > soft * caution_fraction`
  - `min`: `value < soft / caution_fraction` (while still ≥ soft)
  - `range`: CAUTION bands inside the declared `[min, max]` near either bound
- `CompositionScheduler.reload_constraints()` now preserves the `ConstraintEvaluator` warmup step counter. `warmup_exempt` rules continue to behave correctly relative to episode progress after a hot reload. `_last_constraint_status` is also reset on reload.
- Manifest validation now enforces documented top-level fields:
  - `apiVersion` must be exactly `"pamcl/v2"`
  - `kind` must be exactly `"Composition"`
- Deeper structural validation for `control_limits` (each entry must be a dict; `min`/`max` values must be numeric when present). Clear error messages.
- `AuditLogger.read_all()` / `read_by_type()` and the dashboard loader are now resilient to malformed JSON lines (truncated writes, manual edits, etc.). Bad lines are skipped instead of crashing the entire read.
- Dashboard (`serve_dashboard`) now HTML-escapes all dynamic event data in `formatDetails` before using `innerHTML`, reducing risk from untrusted audit logs.
- Friendlier error when agent instantiation fails during `load_composition()` (includes agent id and original exception).
- `pytest.importorskip` guards added to simulator-dependent tests so `pytest` works cleanly in a PAMCL-only checkout.

### Changed / Fixed (from code review)
- Version metadata aligned (`pyproject.toml` now declares 0.4.0 to match `__init__.__version__` and documentation).
- Numerous documentation updates for accuracy (severity logic, validation rules, import side-effects / trust boundary, reload semantics, event types).
- `PAMCL_DEVGUIDE.md` marked as historical (pre-v2 tightly-coupled design). Current authoritative docs are `README.md`, `docs/manifest-spec.md`, and `docs/api-reference.md`.

### Documentation
- Updated severity tables and validation rule lists in `docs/manifest-spec.md` and `docs/api-reference.md`.
- Added prominent obsolete notice + pointers in `PAMCL_DEVGUIDE.md`.
- README quickstart numbers, roadmap, and "Why PAMCL" section refreshed; added explicit note about trusted manifests and v0.4.0 refinements.
- New `CHANGELOG.md` (this file) and retained `FIXES_FROM_REVIEW.md` for detailed review → fix traceability.

**Tests**: 121+ passing (core constraint, loader, scheduler, audit, dashboard, CLI, and v0.3/v0.4 feature coverage).

## Prior releases

See git history and the original `PAMCL_DEVGUIDE.md` (historical) + `req_001.md` for v0.1–v0.3 intent and implementation notes.

The core promise remains: **vendor-agnostic YAML-driven composition with strong auditability and no simulator lock-in in the `pamcl/` package**.

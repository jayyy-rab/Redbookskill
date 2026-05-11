# Function Unification Map (Step1~Step10)

## 1) Primary execution entries

- `scripts/dryrun_step1_10_runner.py::run_strict_dryrun`
  - Current strict Step1~Step10 dry-run orchestrator.
  - Contains step-gate (`success` only -> next step; `failed/manual_review/stopped` -> block downstream).

- `scripts/publish_pipeline.py::main`
  - Real publish-page executor (fill + optional product attach + optional publish click).
  - Used by Step9/Step10 in dry-run runner (with `--preview` to avoid real publish).

- `scripts/xhs_images_to_picset.py::main`
  - Search/download/generate/autocolor chain wrapper used by Step3~Step7.
  - Calls `scripts/picset_automation.py` for UI generation operations.

- `scripts/full_stack_orchestrated.py::main`
  - Alternative class-based orchestrator (Step A/B/C model), overlaps with dryrun runner responsibilities.

- `scripts/smoke_runner.py::main`
  - Smoke batch wrapper for invoking publish pipeline.

- `scripts/bulk_publish_accounts.py::main`
  - Multi-account outer loop wrapper around prepare/publish.

## 2) Step-to-function mapping (current dry-run mainline)

- Step1 Input validate:
  - `scripts/dryrun_step1_10_runner.py::run_strict_dryrun`
  - helper: `_is_readable_image`

- Step2 Search keyword:
  - `scripts/cdp_publish.py::XiaohongshuPublisher.search_feeds`
  - invoked by dryrun runner subprocess

- Step3 Filter/download refs:
  - `scripts/xhs_images_to_picset.py::run_search_covers`
  - helper: `_extract_cover_urls_from_search_stdout`

- Step4~Step7 Picset generation/autocolor:
  - `scripts/xhs_images_to_picset.py::main`
  - `scripts/picset_automation.py::_upload_reference_images_via_cdp`
  - `scripts/picset_automation.py::_fill_prompt_and_generate`
  - `scripts/picset_automation.py::_collect_generated_image_urls`
  - `scripts/xhs_image_autofix.py::pillow_post_adjust`

- Step8 Copy generation:
  - `scripts/douban_promo_copy.py::main`

- Step9 Fill publish page (preview):
  - `scripts/publish_pipeline.py::main`
  - `scripts/cdp_publish.py::XiaohongshuPublisher.publish`

- Step10 Add product verify + publish interception:
  - `scripts/publish_pipeline.py::main`
  - `scripts/cdp_publish.py::XiaohongshuPublisher.click_add_product`
  - `scripts/cdp_publish.py::XiaohongshuPublisher.select_product_with_match`

## 3) Duplicate/overlap hotspots

### A. Orchestrator overlap
- `dryrun_step1_10_runner.py::run_strict_dryrun`
- `full_stack_orchestrated.py::main` (+ `StepAVisualGenerate/StepBCopywriting/StepCPublish`)
- `smoke_runner.py::main`
- `bulk_publish_accounts.py::main`

Risk: different gate rules / different defaults / drift in parameters and status semantics.

### B. Repeated utility logic
- `_dedupe_existing_paths_by_hash` exists in:
  - `scripts/full_stack_orchestrated.py`
  - `scripts/bulk_publish_accounts.py`
- `_validate_publish_images` exists in:
  - `scripts/full_stack_orchestrated.py`
  - `scripts/bulk_publish_accounts.py`

Risk: one side fixed, another side stale.

### C. Status/state reporting overlap
- dryrun runner has custom `state.json` format + pending-fix list logic.
- class workflow runner (`workflow_core.py`) has separate report schema.

Risk: downstream tools parse one format and fail on the other.

## 4) Minimal unification plan (no broad refactor)

### Batch-1 (safe, minimal, recommended first)
1. Keep `dryrun_step1_10_runner.py` as **single source of truth** for Step1~Step10 strict dry-run.
2. Keep `publish_pipeline.py` as **single source of truth** for real page fill / product attach / publish click.
3. In `full_stack_orchestrated.py`, mark role explicitly as A/B/C wrapper only; do not duplicate Step1~Step10 semantics.
4. Extract duplicated helpers (`dedupe + validate_publish_images`) to one shared helper module and import from both callers.

### Batch-2 (after Batch-1 stable)
1. Normalize status enums across orchestrators:
   - `success/failed/manual_review/skipped/blocked_by_previous_failure`
2. Normalize report fields:
   - `step/status/reason/logs/files/functions/screenshot/fix_suggestion`

## 5) Current recommendation

Apply Batch-1 only in next patch round:
- no behavior re-architecture,
- no API contract change,
- no publish logic rewrite,
- only utility dedupe + source-of-truth clarification.

## 6) Batch-1 completion update (current)

Completed:
- Shared media utilities extracted to `scripts/media_path_utils.py`.
- Shared workflow status/next-action helpers extracted to `scripts/workflow_status.py`.
- `scripts/dryrun_step1_10_runner.py` now uses shared status helpers.
- `scripts/full_stack_orchestrated.py` now uses shared status helpers.

Pending for full 100% unification:
- Keep one recommended business entry for customer delivery (currently建议主用 `dryrun_step1_10_runner.py` + `publish_pipeline.py`).
- Optional: normalize report field schema between dryrun and A/B/C wrappers (non-blocking).

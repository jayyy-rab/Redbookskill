# Rule Priority and Classification

This file is the single index for rule files in this repository.
When rules conflict, apply higher priority first.
For category-based lookup, see `docs/RULE_CATALOG.md`.

## Priority Order

### P0 (Hard constraints, always apply first)
- `AGENTS.md`
- `docs/STRICT_WORKFLOW_RULES.md`
- `docs/engineering_rules.md`
- `docs/BUG_FIX_AGENT_RULES.md`
- `docs/P0_RUN_OUTPUT_STABILITY_RULES.md`

### P1 (Scenario hard constraints, apply when scenario matches)
- `docs/BUG_CASE_PICSET_PROMPT_GENERATE_RULE.md`
- `SKILL.md`

### P2 (Product and delivery constraints)
- `docs/PRD.md`
- `docs/ACCEPTANCE.md`
- `plan.md`

### P3 (Reference and operational guidance)
- `README.md`
- `docs/01-overview-routing-constraints.md` to `docs/13-bulk-zero-touch-publish.md`
- `docs/claude-code-integration.md`
- `todo.md`
- `docs/code-review-2026-03-07.md`

## Exclusions
- `scripts/edge_profile/**/LICENSE.md` is third-party license text, not project rule.

## Execution Policy
1. Before any change, check P0 then P1 for matching constraints.
2. If conflict exists inside same priority, stop and propose a merge rule before editing code.
3. After any feature or behavior change, sync docs required by `AGENTS.md`.
4. Keep modifications minimal and avoid broad refactors unless explicitly approved.

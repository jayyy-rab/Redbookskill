# Rule Catalog

This file classifies all rule-related documents in this repository by purpose.
Use this together with `docs/RULE_PRIORITY.md`.

## A. Governance and Execution Baseline

These define how work must be done.

- `AGENTS.md`
- `docs/STRICT_WORKFLOW_RULES.md`
- `docs/engineering_rules.md`

## B. Bug Handling and Quality Guardrails

These define how to handle defects and avoid regressions.

- `docs/BUG_FIX_AGENT_RULES.md`
- `docs/BUG_CASE_PICSET_PROMPT_GENERATE_RULE.md`
- `docs/12-troubleshooting.md`
- `docs/code-review-2026-03-07.md`

## C. Product Scope and Acceptance

These define what to build and what counts as done.

- `docs/PRD.md`
- `docs/ACCEPTANCE.md`
- `plan.md`
- `todo.md`

## D. Skill Runtime Contract

These define how the skill should be triggered and executed.

- `SKILL.md`
- `docs/claude-code-integration.md`

## E. Feature and Operation Playbooks

These are operational guides and scenario playbooks.

- `README.md`
- `docs/01-overview-routing-constraints.md`
- `docs/02-environment-windows-powershell.md`
- `docs/03-browser-session-login-test.md`
- `docs/04-publish-image-and-video.md`
- `docs/05-multi-account-and-cdp-ports.md`
- `docs/06-feeds-search-comments-data.md`
- `docs/07-picset-integration-photoshop.md`
- `docs/08-visual-publish-pipeline-hotspot.md`
- `docs/09-operations-matrix-notes.md`
- `docs/10-douban-promo-copy-api.md`
- `docs/11-topics-publish-closed-loop.md`
- `docs/13-bulk-zero-touch-publish.md`

## F. Exclusions

Not project rules:

- `scripts/edge_profile/**/LICENSE.md`

## Usage Order

1. Resolve conflicts by `docs/RULE_PRIORITY.md`.
2. Use this file (`docs/RULE_CATALOG.md`) to find the right rule set by task type.

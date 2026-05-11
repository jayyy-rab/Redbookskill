# Bug Rule: Picset Prompt + Generate

Scope: only for the bug "prompt not filled / generate button not clicked" in Picset step.

## Root Cause (confirmed)

1. In `scripts/picset_automation.py::_fill_prompt_and_generate`:
   `isDisabled()` used class substring match (`includes("disabled")`), which falsely marks Tailwind classes like `disabled:opacity-50` as disabled.
   Result: prompt textarea is skipped (`inputFound=False`), so prompt is never filled.
2. In the same function:
   generate candidate scoring allowed non-actionable container nodes (`div/span`) to outrank the actual button by area/text score.
   Result: click lands on container text block, not the real generate button.

## Fix Rule

1. Keep fix strictly inside `scripts/picset_automation.py::_fill_prompt_and_generate`.
2. `isDisabled()` must only rely on real disabled signals:
   - `el.disabled`
   - `disabled` attribute
   - `aria-disabled="true"`
   - explicit disabled tokens (e.g. `is-disabled`, `ant-btn-disabled`)
3. Generate candidate scoring must prefer actionable elements (`button/a/role=button/onClick`) and log clicked tag + label.
4. Keep existing validation; do not bypass prompt check or generate candidate scan.

## Regression Test Method

1. Syntax check:
   `python -m py_compile scripts/picset_automation.py`
2. Targeted reproduce/verify (connected to logged-in Picset tab):
   run a minimal script that imports and calls `_fill_prompt_and_generate(...)`.
3. Pass criteria from log/output:
   - `inputFound=True`
   - `promptConfirmed=True`
   - `generateClicked=True`
   - `clickedTag=button`
   - `selectedInputTag=textarea` (or expected editor input)

1. Locate first in `scripts/picset_automation.py::_fill_prompt_and_generate`.
2. After prompt input, verify prompt persistence on the same node; if missing, re-apply once.
3. If generate candidates are empty, perform one scroll-down retry and re-scan candidates.
4. Keep and inspect trigger logs:
   - `inputFound`
   - `promptConfirmed`
   - `generateClicked`
   - `candidatesRetryScrolled`
5. Do not modify publish modules or other workflow steps when this bug is under repair.

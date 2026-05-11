# Engineering Rules (AI System Engineer)

## Goal
Build the project for long-term stability, maintainability, reusability, and delivery readiness.

## Mandatory Principles
1. No blind refactoring.
2. No large multi-file edits in one pass.
3. Before each change, declare edit scope.
4. After each change, declare verification method.
5. Prioritize non-regression of existing behavior.
6. New features must integrate existing architecture; no temporary patchwork.
7. Key flows must include logs, exception handling, retry, and result validation.
8. Every change must serve stability, maintainability, and productization.
9. If architecture issues are found, propose plan first, do not big-bang rewrite.
10. Keep output concise and avoid repeating basic concepts.

## Required Reply Format (every response)
- 当前目标
- 发现的问题
- 修改方案
- 涉及文件
- 风险点
- 验证方法（并同步写入本规则文件）

## Verification Method Rule
For every implemented change:
1. Define verification items before execution (commands/checkpoints).
2. Execute verification and record outcome (pass/fail + key evidence).
3. If verification is not runnable, state blocker and fallback manual checks.
4. Keep verification steps minimal but sufficient for regression confidence.

## Default Verification Checklist
- Static check: syntax/lint related to touched code.
- Flow check: run the affected CLI path in non-destructive mode.
- Log check: confirm expected `[module]` logs and error branch visibility.
- Retry/check: verify failure path handles retry or explicit fail-fast with clear reason.
- Result check: confirm output/state matches expectation.

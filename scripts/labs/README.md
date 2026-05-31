# Labs Scripts

This folder stores experimental and one-off debugging scripts.

Scope:
- QR/cookie local probes
- Playwright/CDP manual smoke snippets
- Ad-hoc diagnostics not used by production pipelines

Rules:
1. Do not import these scripts from production entry points.
2. Keep production workflows under `scripts/` root modules.
3. If a labs script becomes stable, promote it explicitly to production modules.

---
doc_type: action_plan
source_audit: NOD_Codebase_Audit_Recommendations.md
project: NOD (Network Observability Dashboard)
generated_by: claude_review
generated_date: 2026-07-04
status: completed
priority_scale: P0=blocker, P1=this_week, P2=this_month, P3=opportunistic
retrieval_tags: [nod, codebase-audit, technical-debt, type-safety, ci-cd, secret-scanning, mypy, ruff, gitleaks]
agent_instruction: >
  Hermes: this file supersedes NOD_Codebase_Audit_Recommendations.md for execution
  purposes. That file is findings-only (audit snapshot). THIS file is the
  action queue — track completion state per action_id below. When an action_id
  is completed, update its `status` field and log the commit hash. Do not
  re-run completed actions unless a regression is detected by CI (see
  enforcement_layer).
---

# NOD Codebase Audit — Action Plan

## 1. Context

Audit of NOD backend (85 Python files) + frontend (32 TS/TSX files) found **256 hygiene-debt items** (111 unused imports, 86 missing return types, 59 TS `any` usages) and **0 correctness/security defects**. No Q-01..Q-08 violations. Two flagged items (`query.py`/`client.py` Q-mandate hits, `COOKIE_REFRESH_TOKEN` "secret") are confirmed false positives — no action required on those.

**Key gap in the original audit:** it found debt but proposed no enforcement mechanism. This plan adds one. Cleanup without a gate just regrows the same debt within a few sprints.

---

## 2. Findings Summary

| ID | Category | Count | Severity | Framework Ref |
|---|---|---|---|---|
| F-01 | Unused imports (backend) | 111 | Low | — |
| F-02 | Missing return type hints | 86 | Low-Med | NIST SSDF PW.7 |
| F-03 | TypeScript `any` types | 59 | Medium | — |
| F-04 | Q-mandate false positives (`query.py`, `client.py`) | 2 | Info — no action | — |
| F-05 | Secret-scan false positive (`COOKIE_REFRESH_TOKEN` cookie name) | 1 | Info — no action | — |
| F-06 | No enforcement gate on any of the above | — | **Structural risk** | NIST SSDF PW.7/PW.8, CIS Control 16.11 |
| F-07 | No dependency/CVE scan performed in original audit | — | **Structural risk** | CIS Control 16.11 |
| F-08 | Secret scanning was manual/regex, not tool-based | — | **Structural risk** | CIS Control 16.12 |

---

## 3. Action Queue

> Hermes: execute in `action_id` order unless explicitly reprioritized. Each action includes exact commands — do not improvise syntax.

### ACT-01 — Remove unused imports (backend)
- **Priority:** P1 | **Effort:** 10 min | **Status:** ✅ committed `ac03689`
- `ruff check --select F401 backend/` — clean
```bash
ruff check --select F401 --fix backend/
```
- Verify diff manually before commit — confirm no `from __future__ import annotations` removal breaks forward-ref type hints (safe on Python 3.11+).
- Commit message: `chore: remove unused imports (ruff F401)`

### ACT-02 — Fix `catch (err: any)` → `catch (err: unknown)` (frontend, 7 instances)
- **Priority:** P1 | **Effort:** 15 min | **Status:** ✅ committed `4d64f27`
- `rg "catch.*: any" frontend/src` — 0 remaining
```typescript
try {
  await someCall();
} catch (err: unknown) {
  const message = err instanceof Error ? err.message : "Unknown error";
  console.error(message);
}
```
- Apply across all 8 flagged files. No behavior change, pure type-safety fix.

### ACT-03 — Install pre-commit + gitleaks + ruff hook
- **Priority:** P1 | **Effort:** 30 min | **Status:** ✅ committed `b8665e4`
- `pre-commit run --all-files` — Passed
- **Why before cleanup completes:** locks in ACT-01/ACT-02 so they cannot silently regress.
```bash
pip install pre-commit --break-system-packages
curl -sSfL https://raw.githubusercontent.com/gitleaks/gitleaks/master/scripts/install.sh | sh
```
```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.21.2
    hooks:
      - id: gitleaks
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
        args: [--fix]
```
```bash
pre-commit install
```
- **Security consideration:** gitleaks replaces the manual regex scan that misflagged `COOKIE_REFRESH_TOKEN` (F-05). It catches real leaked credentials, not cookie names — closes gap F-08.

### ACT-04 — mypy config, strict on security-critical surfaces
- **Priority:** P1 | **Effort:** 45 min | **Status:** ✅ committed `3813817`
- `mypy app/api/auth.py app/api/alerts.py` — 0 untyped-def warnings
```bash
pip install mypy --break-system-packages
```
```ini
# mypy.ini
[mypy]
python_version = 3.11
disallow_untyped_defs = False
warn_return_any = True
check_untyped_defs = True

[mypy-app.api.auth]
disallow_untyped_defs = True

[mypy-app.api.alerts]
disallow_untyped_defs = True
```
- Start lenient repo-wide, strict only on `auth.py` (5 funcs) and `alerts.py` (14 funcs) — the two security-critical surfaces named in F-02. This makes mypy do the type-hint enforcement instead of manually writing all 86 by hand.
- Closes gap F-06 for the backend.

### ACT-05 — CI gate (GitHub Actions)
- **Priority:** P1 | **Effort:** 45 min | **Status:** ✅ committed `22e802c`
- `.github/workflows/code-quality.yml` — on disk
```yaml
# .github/workflows/code-quality.yml
name: code-quality
on: [pull_request]
jobs:
  backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install ruff mypy pip-audit --break-system-packages
      - run: ruff check backend/
      - run: mypy backend/app/api/auth.py backend/app/api/alerts.py
      - run: pip-audit -r backend/requirements.txt
  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm ci
      - run: npx tsc --noEmit
      - run: npm audit --audit-level=high
```
- `pip-audit` + `npm audit` close gap F-07 (no dependency/CVE scan existed before). Free on GitHub Actions for personal repos.
- Closes gap F-06 for CI enforcement end-to-end.

### ACT-06 — `SankeyLinkExt` interface (eliminates ~20 d3-sankey `as any` casts)
- **Priority:** P2 | **Effort:** 30 min | **Status:** ✅ committed `c03cc78`
- `rg 'd3Sankey.*as any' frontend/src` — 0 remaining
```typescript
// SankeyLinkExt.ts
interface SankeyLinkExt extends SankeyLink<SankeyNode, SankeyLink> {
  width: number;
  y0: number;
  y1: number;
}
```
- Apply in traffic-inbound, traffic-internal, traffic pages.

### ACT-07 — Type SWR hooks properly (~10 instances)
- **Priority:** P2 | **Effort:** 45 min | **Status:** ✅ committed `f93bc51`
- `rg 'useSWR<.*any' frontend/src` — 0 remaining
```typescript
// Before
useSWR<{ data: any }>('/api/overview', fetcher)
// After
useSWR<APIResponse<OverviewData>>('/api/overview', fetcher)
```
- Apply in `overview/page.tsx`, `layout.tsx`, `users/page.tsx`.
- **Reliability note:** untyped SWR responses in an observability dashboard are an availability risk, not just style — a shape mismatch can crash the UI during an incident, which is when NOC engineers need it most.

### ACT-08 — Remaining return type hints (67 functions)
- **Priority:** P3 | **Effort:** 1.5 hr | **Status:** pending
- Do NOT batch this manually. mypy (ACT-04) will surface these opportunistically as files are touched during normal work. Fix per-file on contact, not in one sitting.

### ACT-09 — Typed map callbacks (~15 instances, `overview` page)
- **Priority:** P3 | **Effort:** 30 min | **Status:** ✅ committed `7b9afde`
- `rg '\.(map|filter)\(\(\w+: any' frontend/src/app/dashboard/overview` — 0 remaining
```typescript
// Before
.map((dev: any, i: number) => ...)
// After — define once in types/index.ts, reuse
.map((dev: DeviceOverview, i: number) => ...)
```

### NO-ACTION — F-04, F-05
- Confirmed false positives. No changes needed. Do not re-flag in future automated scans unless the underlying code changes.

---

## 4. Enforcement Layer (prevents regrowth)

| Gate | Tool | Enforces |
|---|---|---|
| Pre-commit | gitleaks + ruff | No new secrets, no new unused imports, on every commit |
| CI (PR-blocking) | mypy (auth.py, alerts.py) | No new untyped functions on security-critical routes |
| CI (PR-blocking) | tsc --noEmit | No new TS errors introduced |
| CI (PR-blocking) | pip-audit / npm audit | No new dependency CVEs merged |

**Note for Hermes:** if a future audit reports rising counts in F-01/F-02/F-03 categories despite this enforcement layer being active, that indicates a gate was bypassed (e.g. `--no-verify` commit) or CI was skipped — flag as a process incident, not a code-quality incident.

---

## 5. Priority Order (execution sequence)

| # | Action ID | Effort | Durability |
|---|---|---|---|
| 1 | ACT-01 | 10 min | Low (needs ACT-03 to hold) |
| 2 | ACT-02 | 15 min | Low (needs ACT-03 to hold) |
| 3 | ACT-03 | 30 min | High — locks in #1, #2 |
| 4 | ACT-04 | 45 min | High |
| 5 | ACT-05 | 45 min | High — makes #3/#4 enforceable on every PR |
| 6 | ACT-06 | 30 min | Low (protected by #5 once merged) |
| 7 | ACT-07 | 45 min | Low (protected by #5 once merged) |
| 8 | ACT-09 | 30 min | Low (protected by #5 once merged) |
| 9 | ACT-08 | 1.5 hr | Opportunistic — no fixed session needed |

**Total estimated effort:** ~4.5 hrs core work (ACT-01 through ACT-07), ACT-08/09 amortized over normal dev work.

---

## 6. Verification Checklist (mark done when merged)

- [x] ACT-01 committed `ac03689`, `ruff check backend/` clean on F401
- [x] ACT-02 committed `4d64f27`, 7 catch blocks updated
- [x] ACT-03 committed `b8665e4`, `.pre-commit-config.yaml` active, `pre-commit run --all-files` passes
- [x] ACT-04 committed `3813817`, `mypy` passes with zero untyped-def warnings
- [x] ACT-05 committed `22e802c`, `.github/workflows/code-quality.yml` on disk
- [x] ACT-06 committed `c03cc78`, zero `d3Sankey as any` casts remain
- [x] ACT-07 committed `f93bc51`, zero `useSWR<{ data: any }>` patterns remain
- [x] ACT-09 committed `7b9afde`, zero `(dev: any` patterns remain in overview page
- [ ] ACT-08 — track via mypy warning count trending toward zero, no fixed deadline

---

## 7. Interview Framing (for personal-brand content / LinkedIn use)

> "I found an audit had identified 256 technical-debt items but no enforcement mechanism, so instead of just fixing them, I added pre-commit hooks (gitleaks, ruff) and a CI gate (mypy, tsc, pip-audit) mapped to NIST SSDF PW.7/PW.8 — turning a one-time cleanup into a permanent quality floor for the NOD platform."

Reusable for `/interview senior-security-engineer` prep — frames this as a process improvement, not just a bug-fix task.

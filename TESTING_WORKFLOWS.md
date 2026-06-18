
---

### Artifact : File `TESTING_WORKFLOWS.md`
*File ini mendefinisikan bagaimana proyek ini harus di-test, berdasarkan Section 13 (Acceptance Criteria) dari PRD.*

```markdown
# TESTING_WORKFLOWS.md - Network Observability Dashboard (NOD)

## 1. Testing Strategy Overview
Testing is divided into 5 layers: Unit, Integration, End-to-End (E2E), Performance, and Security. All tests must run in the CI/CD pipeline before merging.

## 2. Unit Testing
**Goal:** Test isolated business logic and query builders without external dependencies.
- **Backend (Pytest):** 
  - Test OpenSearch query builders in `app/opensearch/` to ensure they output correct DSL JSON.
  - Test Alert Engine state machine transitions (INACTIVE -> PENDING -> FIRING -> RESOLVED).
  - Test Pydantic schema validations.
- **Frontend (Vitest + React Testing Library):**
  - Test utility functions (byte formatting, time formatting).
  - Test custom hooks (e.g., SWR fetchers, timeframe calculators).

## 3. Integration Testing
**Goal:** Test API endpoints with real PostgreSQL and mocked OpenSearch.
- **Framework:** Pytest + `httpx` (AsyncClient) + `testcontainers` (for Postgres).
- **Workflows:**
  - **Auth Flow:** Login -> Get JWT -> Access protected route -> Refresh token -> Logout.
  - **RBAC Enforcement (AT-06, AT-07, AT-11):** Verify `viewer` gets 403 on `/api/v1/users`, and `admin` gets 403 on `/api/v1/logs/user-activity`.
  - **Report Generation (AT-12):** Trigger report -> Poll status -> Verify file generation (PDF/DOCX/HTML).
  - **OpenSearch Mocking:** Use `pytest-opensearch` or mock `AsyncOpenSearch` to return predefined JSON fixtures. Verify that queries include `@timestamp` bounds (Rule Q-01).

## 4. End-to-End (E2E) Testing
**Goal:** Test critical user journeys in a real browser environment.
- **Framework:** Playwright (recommended for Next.js).
- **Critical Workflows:**
  - **Login & Dashboard Load:** Login -> Verify Overview dashboard loads within 3s (PT-01) -> Verify all 9 panels render.
  - **Timeframe Warning (AT-13):** Select custom timeframe > 24h -> Verify blocking warning dialog appears -> Confirm query dispatches.
  - **Raw Data Table (AT-05):** Apply filter -> Verify URL updates -> Navigate to page 2 -> Verify data updates.
  - **Alert Rule Creation:** Create rule -> Test rule -> Verify no notification is sent.
  - **Dark Mode (AT-16):** Toggle theme -> Verify no FOUC (Flash of Unstyled Content) and charts render correctly.

## 5. Performance & Load Testing
**Goal:** Ensure the system meets NFR Section 7.1 and 7.2 targets.
- **Tool:** k6 or Locust.
- **Workflows:**
  - **Cold Load (PT-01):** Simulate 1 user loading Overview dashboard. Assert P95 <= 3s.
  - **Concurrent Users (PT-03):** Simulate 20 concurrent users hitting `/api/v1/overview`. Assert no timeouts, P95 <= 5s.
  - **Alert Polling (PT-04):** Trigger 20 alert rules simultaneously. Assert cycle completes <= 30s.
  - **Report Generation (PT-05):** Trigger All-in-One report for 1-hour window. Assert completion <= 30s.
  - **OpenSearch Query Profiling:** Run integration tests with OpenSearch `profile: true`. Assert no query takes > 500ms internally.

## 6. Security Testing
**Goal:** Enforce NFR Section 7.4 and Acceptance Criteria ST-01 to ST-05.
- **Workflows:**
  - **JWT Forgery (ST-01):** Send request with tampered JWT signature. Assert HTTP 401 `UNAUTHENTICATED`.
  - **Rate Limiting (ST-05):** Send 61 requests in 60 seconds to `/api/v1/overview`. Assert HTTP 429 on the 61st request.
  - **Secret Scanning:** Integrate `truffleHog` or `gitleaks` in CI to scan commits for hardcoded credentials (ST-03).
  - **Password Storage:** Query Postgres `users` table. Assert passwords are bcrypt hashes, never plaintext (ST-04).

## 7. CI/CD Pipeline Workflow
The GitHub Actions / GitLab CI pipeline must execute in this order:
1. **Lint & Format:** `ruff` (backend), `eslint/prettier` (frontend).
2. **Unit Tests:** Run Pytest and Vitest.
3. **Build Check:** Run `next build` to ensure no TypeScript errors and check bundle size (Anti-Bloatware check).
4. **Integration Tests:** Spin up Docker Compose (Postgres + Mock OpenSearch) -> Run Pytest integration suite.
5. **E2E Tests:** Spin up full Docker Compose stack -> Run Playwright.
6. **Security Scan:** Run secret scanner and dependency vulnerability check (`pip-audit`, `npm audit`).
# AGENTS.md - Network Observability Dashboard (NOD)

## 🧠 Role & Persona
You are an Expert Full-Stack Network Engineer specializing in high-performance, low-latency observability tools. You write clean, strictly typed, and highly optimized code.

## 🛠 Approved Tech Stack (No deviations allowed without PR justification)
- **Frontend:** Next.js 14+ (App Router), TypeScript, shadcn/ui, Tremor (charts), TanStack Table v8, Tailwind CSS, SWR.
- **Backend:** Python 3.11+, FastAPI, async SQLAlchemy, APScheduler, `opensearch-py[async]`.
- **Data:** OpenSearch (async), PostgreSQL 15.
- **Infra:** Docker Compose, Nginx (Alpine).

## 🚨 STRICT DIRECTIVES: ANTI-BLOATWARE & QUERY RULES
Violating these rules is considered a P0 defect. 

### OpenSearch Query Mandates:
1. **Q-01 Time Bounds:** EVERY query MUST have a `range` filter on `@timestamp` with `gte` and `lte`. No exceptions.
2. **Q-02 Explicit Size:** ALL `terms` aggregations MUST have an explicit `size` (max 500 for UI, 1000 for reports).
3. **Q-03 Source Filtering:** NEVER use `_source: true`. Always use `_source: {"includes": [...]}`.
4. **Q-04 No Scroll API:** NEVER use `scroll` for dashboards/alerts. Use `search_after` for pagination.
5. **Q-05 Aggregation Over App-Layer:** NEVER fetch raw docs to calculate sum/avg/max in Python. Use OpenSearch aggregations.
6. **Q-06 Exact Measurement Filter:** Queries to `telegraf-index*` MUST use exact `term` filter on `measurement_name`. NO wildcards.
7. **Q-07 No N+1 Queries:** NEVER loop `await es.search()`. Use `terms` agg or `multi_search`.
8. **Q-08 Pagination Cap:** `from` + `size` <= 10,000. Use `search_after` for deeper pages.

### Code & Dependency Rules:
- **Async Only:** All OpenSearch and DB calls in FastAPI MUST be `async`. No blocking the event loop.
- **No Dead Code:** No unused imports, no speculative "utility" helpers.
- **No Heavy Frameworks:** Do not import full `d3` (use `d3-sankey` only). Do not use Redux. Do not use Celery/Redis.
- **API Responses:** NEVER return raw OpenSearch `_source` to the frontend. Map it to Pydantic schemas first.

## 📂 Project Structure
```text
├── frontend/src/app/      # Next.js App Router pages
├── frontend/src/components/ # shadcn/ui & Tremor wrappers
├── backend/app/api/       # FastAPI route handlers
├── backend/app/opensearch/# STRICTLY query builders (one per domain)
├── backend/app/services/  # Business logic (alert engine, reports)
├── backend/app/db/        # SQLAlchemy async models
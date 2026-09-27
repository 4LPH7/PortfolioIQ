# Phase 2: Testing & CI/CD — Research Report

**Researched:** 2026-09-27
**Domain:** Test Automation, PostgreSQL Fixtures, Code Quality, Container Validation, CI/CD Pipelines

---

## 1. PostgreSQL Test Harness & Transactional Rollbacks

### 1.1 Architectural Pattern: Nested Savepoints in SQLAlchemy 2.0
In SQLAlchemy 2.0, tests achieve complete database isolation without truncating tables or dropping schemas by utilizing `join_transaction_mode="create_savepoint"`.
- When an application session is instantiated with `Session(bind=connection, join_transaction_mode="create_savepoint")`, any `session.commit()` inside application code (such as in `src.db.connection.get_db_session()` or `src.db.repository`) merely releases a PostgreSQL `SAVEPOINT`. It **never** commits the outer database transaction.
- When the test function finishes, the fixture rolls back the outer transaction via `trans.rollback()`, discarding all inserted audit records, price partition entries, and triggers executed during the test.

### 1.2 Session-Scoped Migration Runner Execution
- `db/run_migrations.py` contains `run_migrations()` which idempotently checks `schema_migrations`, verifies SHA-256 checksums, and executes unapplied migrations in order.
- In `tests/conftest.py`, a `@pytest.fixture(scope="session", autouse=True)` invokes `run_migrations()` once before any test executes, provided PostgreSQL is reachable.

### 1.3 Offline DB Detection & Mark Auto-Skipping (`@pytest.mark.postgres`)
- A fast connection probe using `psycopg2.connect(..., connect_timeout=1)` tests if PostgreSQL is listening on `DATABASE_URL`.
- Hook `pytest_runtest_setup(item)` checks if the test is marked `@pytest.mark.postgres`:
  - **Local Run without DB:** Skips the test with `pytest.skip("PostgreSQL is not reachable locally. Skipping @pytest.mark.postgres test.")`.
  - **CI Run (`CI=true`):** Fails fast with `pytest.fail("PostgreSQL is unavailable in CI environment!")`.
- Existing test `tests/test_phase0_safety.py::test_gatekeeper_writes_canonical_order_and_validation_rows` (which was previously hardcoded with `@pytest.mark.skipif(not os.environ.get("CI"))` because it wrote immutable rows) can be marked with `@pytest.mark.postgres` and run safely both locally and in CI thanks to the rollback fixture.

---

## 2. CI Pipeline Quality Gates (`.github/workflows/ci.yml`)

### 2.1 PostgreSQL Service Container in GitHub Actions
Standardize credentials with `docker-compose.yml` (`portfolioiq_user` / `portfolioiq_pass_change_me` / `portfolioiq`):
```yaml
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_USER: portfolioiq_user
          POSTGRES_PASSWORD: portfolioiq_pass_change_me
          POSTGRES_DB: portfolioiq
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U portfolioiq_user -d portfolioiq"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
```

### 2.2 Unified Ruff Configuration (`pyproject.toml`)
Create `pyproject.toml` with rule sets (`E`, `F`, `W`, `I`, `B`, `UP`) and 100 char line-length:
```toml
[tool.ruff]
target-version = "py312"
line-length = 100
exclude = [
    ".git",
    ".pytest_cache",
    "__pycache__",
    "build",
    "dist",
    "venv",
    ".venv",
]

[tool.ruff.lint]
select = [
    "E",   # pycodestyle errors
    "W",   # pycodestyle warnings
    "F",   # pyflakes
    "I",   # isort
    "B",   # flake8-bugbear
    "UP",  # pyupgrade
]
ignore = [
    "E501",  # line length handled by ruff format
    "B008",  # do not perform function calls in argument defaults
]

[tool.ruff.lint.isort]
known-first-party = ["src"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
line-ending = "auto"
```

In CI, run:
```bash
ruff check .
ruff format --check .
```
*(Remove `black` from `requirements.txt` to eliminate redundant dependencies).*

### 2.3 Dependency Vulnerability Audit (`pip-audit`) & Pip Caching
- Add `pip-audit>=2.7.3` and `pytest-cov>=6.0.0` to `requirements.txt`.
- In GitHub Actions, cache pip packages via `actions/setup-python@v7`:
```yaml
      - name: Set up Python 3.12
        uses: actions/setup-python@v7
        with:
          python-version: '3.12'
          cache: 'pip'
          cache-dependency-path: 'requirements.txt'

      - name: Run dependency vulnerability audit
        run: pip-audit -r requirements.txt --desc
```

### 2.4 Test Coverage Thresholds (Overall 75% + Critical Paths 85%)
Configure `pyproject.toml` for coverage:
```toml
[tool.coverage.run]
source = ["src"]
omit = [
    "*/__pycache__/*",
    "*/tests/*",
]

[tool.coverage.report]
fail_under = 75
show_missing = true
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
    "if TYPE_CHECKING:",
]
```

Enforce >= 85% coverage on safety-critical modules (`src/execution/`, `src/config/`, `src/api/middleware.py`) via `scripts/check_critical_coverage.py`.

### 2.5 Container Build Validation (`docker build .`)
Run as a standalone parallel job in CI so container errors fail without waiting for deployments:
```yaml
  docker-build:
    runs-on: ubuntu-latest
    name: Validate Docker container build
    needs: secret-scan
    steps:
      - uses: actions/checkout@v7
      - name: Build Docker image
        run: docker build -t portfolioiq:ci .
```

---

## 3. Deployment Smoke Testing & Rollback Automation

### 3.1 Design of `scripts/smoke_test.py`
Zero external dependencies (uses standard library `urllib.request` + `json` + `time` + `argparse`):
- Accepts `--base-url`, `--timeout` (default: 60s), `--interval` (default: 5s).
- Polls `/api/v1/health` and asserts HTTP 200, `status == "ok"`, and `db == True`.
- Polls `/api/v1/market/status` and asserts HTTP 200 and `ok == True`.
- Uses exponential retry backoff (5s, 7.5s, 11s, up to 15s max interval) to handle platform cold starts.
- Returns exit code 0 on success, exit code 1 on timeout or failure.

### 3.2 Integration & Automated Rollback in GitHub Actions
**Crucial Finding:** The Railway CLI has no native `railway rollback` command (it is dashboard-only or GraphQL). Per Decision D-07, the reliable automated rollback mechanism redeploys the previous known-good Git commit SHA:
- In `actions/checkout@v7`, set `fetch-depth: 2` so `HEAD~1` is available in Git history.
- If the smoke test step fails, retrieve `${{ github.event.before }}` (or `git rev-parse HEAD~1`), check out that commit, and execute `railway up --detach`. Then fail the workflow to alert the team.

---

## 4. Pre-commit & Local Developer Workflow

Update `.pre-commit-config.yaml` to run Ruff linter (with `--fix`), Ruff formatter, and Gitleaks:
```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.4
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.30.1
    hooks:
      - id: gitleaks
```

---

## 5. Wave Decomposition for Phase 2

1. **Plan 02-01: Toolchain Modernization & PostgreSQL Test Harness**
   - Requirements & pyproject.toml configuration (`tool.ruff`, `tool.coverage`).
   - `tests/conftest.py` with session migrations, transaction rollback savepoints, and `@pytest.mark.postgres` auto-skip.
   - Update `.pre-commit-config.yaml`.
2. **Plan 02-02: Safety-Critical Test Expansion & Ruff Codebase Normalization**
   - Codebase normalization with `ruff check --fix` and `ruff format`.
   - Comprehensive unit/integration tests for execution validators, config validation, and API middleware.
   - `scripts/check_critical_coverage.py` ensuring >= 85% critical and >= 75% overall coverage.
3. **Plan 02-03: Post-Deployment Smoke Testing & Failure Rollback**
   - `scripts/smoke_test.py` with retry backoff and `/api/v1/` validation.
   - `tests/test_smoke_test.py` unit testing the smoke test runner.
4. **Plan 02-04: GitHub Actions CI/CD Pipeline & Branch Protection**
   - Complete `.github/workflows/ci.yml` reorganization: `secret-scan`, `lint-and-audit`, `docker-build`, `test`, `deploy-railway` (with smoke test & rollback), `deploy-netlify`.
   - Documentation of branch protection rules on `main`.

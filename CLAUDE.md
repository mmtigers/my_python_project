# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository overview

This is a personal monorepo of three independent subsystems that cooperate over a shared SQLite DB, a NAS mount, and REST APIs. There is no root-level build; each subsystem is developed and tested from its own directory.

| Subsystem | Path | Stack | Role |
| --- | --- | --- | --- |
| Backend core | `MY_HOME_SYSTEM/` | Python 3.11, FastAPI, SQLite | IoT device control, environment logging, LINE/Discord notifications, Family Quest API |
| Frontend | `family-quest/` | React 18, TypeScript, Vite, Tailwind | RPG-style family task/quest tracker PWA, served by the backend |
| Batch jobs | `DDD/` | Python, yt-dlp | Unrelated video/content scraping and download automation |

`docs/specifications/` holds one reverse-engineered Markdown spec per source file (see "Spec-drift convention" below) — check there for a deep-dive on any given file before reading the whole thing, and `docs/specifications/全体設計書.md` for the full cross-subsystem architecture and data flow.

## Commands

### MY_HOME_SYSTEM (backend)

Run from `MY_HOME_SYSTEM/`:

```bash
pip install -r requirements.txt -r requirements-dev.txt

# Full test suite (async tests run automatically via pytest.ini's asyncio_mode=auto)
python -m pytest tests/ -v

# Single test file / single test
python -m pytest tests/test_quest_service.py -v
python -m pytest tests/test_quest_service.py::test_something -v

# With coverage, matching CI's threshold
python -m pytest tests/ --cov=. --cov-report=term-missing --cov-fail-under=45

# Lint (CI only blocks on undefined-name/syntax errors; full report is informational)
ruff check . --select F821,F822,F823,E9
ruff check .

# Security scan
bandit -r . -x ./tests -lll

# Run the server directly (dev)
python unified_server.py   # binds 0.0.0.0:8000
```

Tests need these env vars set (CI sets them; set locally too):
```bash
export SQLITE_DB_PATH=":memory:"
export NAS_MOUNT_POINT="./tmp_nas"
export NOTIFICATION_TARGET="none"
```

`tests/conftest.py` provides an `isolated_db` fixture (fresh SQLite file per test, schema initialized via `init_unified_db.init_db()`, `config.SQLITE_DB_PATH` monkeypatched) and an `api_client` fixture (a `TestClient` around `unified_server.app` that does **not** run the `lifespan` context, so background subprocesses like the camera monitor/scheduler never spawn during tests). Prefer these fixtures for new tests; older test files predate them and instead copy-paste their own `config.SQLITE_DB_PATH` override + `init_unified_db.init_db()` in `setUp`/`setup_method` — don't refactor those in passing, just follow the existing pattern in whichever file you're editing.

`conftest.py` also blanks all Discord/LINE webhook/token env vars at import time, before `config` loads — this exists because a local `.env` with real credentials once caused tests to fire real notifications. Never bypass this when writing tests that exercise `notification_service`/`line_service` code paths.

### family-quest (frontend)

Run from `family-quest/`:

```bash
npm run dev      # Vite dev server with HMR
npm run build    # tsc -b && vite build -> dist/
npm run lint     # ESLint
```

The build output `dist/` is served directly by the backend (`unified_server.py` mounts `QUEST_DIST_DIR`, default `../family-quest/dist`, at `/quest`) — **build completion is deployment**, there is no separate deploy/restart step. `./deploy.sh` runs the build; a local `.git/hooks/post-merge` hook (not tracked in git, must be reinstalled after a fresh clone) auto-runs it when `git pull` touches `family-quest/`.

### DDD (batch jobs)

Run from `DDD/`: `pip install -r requirements.txt`, then invoke scripts directly (e.g. `python batch_download_discord.py`). Ruff/Bandit run the same way as MY_HOME_SYSTEM (see CI below), scoped to this directory.

### CI (`.github/workflows/test.yml`)

Four independent jobs: `lint` (ruff on both `MY_HOME_SYSTEM` and `DDD`, only `F821,F822,F823,E9` block the PR), `test` (pytest + coverage, `--cov-fail-under=45`, `MY_HOME_SYSTEM` only), `security` (bandit + pip-audit, only bandit High blocks), `frontend` (`npm ci && npm run build` in `family-quest`, i.e. the TS typecheck via `tsc -b` is the gate). Two more workflows (`spec-drift-pr-check.yml`, `spec-drift-weekly-audit.yml`) run `.github/scripts/check_spec_drift.py` — these are always non-blocking (exit 0) regardless of findings.

## Architecture

### MY_HOME_SYSTEM: request flow and layering

`unified_server.py` is the single FastAPI entry point. On startup (`lifespan`) it applies pending SQL migrations, then spawns `monitors/camera_monitor.py` and `scheduler_boot.py` as **separate subprocesses** (not asyncio tasks) — this is why `tests/conftest.py`'s `api_client` fixture avoids running `lifespan` at all. Routers are thin: `routers/*.py` parse/validate the request and delegate to `services/*.py` for logic, which in turn call `core/database.py` for persistence. Follow that layering when adding endpoints rather than putting logic directly in a router.

Two custom middlewares matter for any new endpoint:
- `ip_restriction_middleware` logs (but currently does not block) non-private-network requests, except it hard-allows `/webhook/switchbot` and `/callback/line` unconditionally since those must accept external traffic. Real access control is delegated to Cloudflare Access at the edge.
- CORS origins live in exactly one place, `config.CORS_ORIGINS` (`ALLOW_ALL_ORIGINS=true` env var overrides to `["*"]`). Do not add a second hardcoded origin list in `unified_server.py` — a past bug had two separate lists where only one was actually wired up.

The `/quest/{full_path}` and `/camera/{full_path}` routes serve the `family-quest` SPA build as static files with a path-traversal guard (`os.path.commonpath` check against the realpath'd dist dir) and fall back to `index.html` for client-side routing.

### Database

SQLite only, single file at `config.SQLITE_DB_PATH` (defaults to `home_system.db` next to `config.py`; overridable via the `SQLITE_DB_PATH` env var, which CI sets to `:memory:` — though the `isolated_db` fixture and most existing tests instead monkeypatch/reassign `config.SQLITE_DB_PATH` directly to a per-test temp file). `core/database.py`'s `get_db_cursor()` context manager is the standard way to read/write: it retries on `sqlite3.OperationalError` ("database is locked"), sets WAL mode + foreign keys, and rolls back on exception. Use it (or `save_log_generic`/`save_log_async` for simple inserts) instead of opening raw `sqlite3.connect()` calls in new code.

Schema changes go through **`migrations/NNNN_description.sql`** (zero-padded, applied in filename order by `core/migrations.py`'s `apply_pending_migrations()`, tracked in a `schema_migrations` table). Write `ALTER TABLE ... ADD COLUMN` first, data-migration statements after — a migration must be safe to re-run against a DB where the column already exists (the runner treats "duplicate column"/"already exists" errors as already-applied and continues; any other `OperationalError` is a hard failure). This is the current convention; `services/quest_service.py`'s older `sync_master_data()` still has some legacy runtime "try SELECT, ALTER on failure" schema checks kept only for backward compatibility with pre-migration deployments — don't imitate that pattern for new changes. See `MY_HOME_SYSTEM/migrations/README.md`.

### Configuration

`config.py` is one large module of module-level constants loaded from `.env` (via `python-dotenv`) plus two optional local JSON overlays that are gitignored and **not required for the app to boot**:
- `devices.json` — camera/IoT device definitions, validated through Pydantic models (`CameraConfig`, `DeviceConfig`).
- `family_members.local.json` — per-member display data (age, etc.); merged onto the placeholder `FAMILY_SETTINGS["styles"]` dict. Member **names** themselves stay hardcoded in `config.py` (not moved to the local override) because LINE bot message matching (`handlers/line_handler.py`) does substring matching against these exact names — renaming/removing them there breaks that logic.

When adding a new external integration, add its secrets/URLs to `config.py` following the existing numbered-section layout (see the module docstring's table of contents) and add a placeholder entry to `.env.example`.

### Family Quest (frontend) structure

`src/App.tsx` is the root component holding top-level UI state (`viewMode`, `activeTab`, `currentUserIdx`); it mounts feature screens directly per tab rather than using a router. `src/features/{quest,family,shop,camera}/` group feature-specific components/hooks; `src/hooks/useGameData.ts` (React Query) is the data-fetching layer talking to the backend's `/api/quest/*` endpoints — check it first when the frontend needs a new API field, since it's also the closest thing to a typed contract with the FastAPI backend (there is no generated OpenAPI-to-TS pipeline yet). `src/context/` holds cross-cutting UI state (settings, toasts). Confirmation/alert dialogs must use the app's own `ConfirmModal`/`MessageModal` components — `window.confirm()`/`alert()` were fully removed in favor of these.

Note: several features described in older docs (boss battles, equipment, guild, mileage, weekly rankings) were deliberately deleted in an August 2026 refactor (see `docs/specifications/全体設計書.md`'s revision note and commits `d1599d6`/`ffdc8c2`/`1818d5a`). Don't resurrect that functionality or reference it as if it still exists.

### DDD batch jobs

Independent of the other two subsystems aside from sharing the NAS mount (`nas_monitor.py` in MY_HOME_SYSTEM can throttle/alert when DDD fills up NAS capacity). `batch_download_discord.py` uses a `DownloadStrategy` strategy pattern (`UniversalYtDlpStrategy` vs `ScrapingStrategy`) and an `fcntl.flock` lock file to prevent concurrent runs — follow the same locking approach for any new long-running cron-style script here.

## Spec-drift convention

`docs/specifications/` mirrors the source tree: one Markdown file per source file under `MY_HOME_SYSTEM/*.py`, `DDD/*.py`, and `family-quest/src/**/*.{ts,tsx,js,jsx}` (tests, migrations, `__init__.py`, and `.d.ts` files are excluded). `.github/scripts/check_spec_drift.py` checks this on every PR (new/changed source files without a correspondingly-updated spec doc are flagged) and in a weekly full-repo audit — **both checks are non-blocking by design**, but when you add or meaningfully change a source file that has a corresponding spec doc, update that doc in the same PR to avoid drift being reported. New files without a doc yet don't strictly require creating one, but it's the established practice in this repo.

## Notable conventions

- Comments and docstrings throughout the Python and shell code are written in Japanese; match that when editing existing files. English is fine for new, unrelated files.
- Long-running host scripts (`start_all.sh`, `run_task.sh`) assume a fixed deployment path (`/home/masahiro/develop/...`) and a `.venv` — these are Raspberry Pi deployment scripts, not portable across environments; don't assume their paths apply in CI or local dev.
- `BACKUP_FILES` in `config.py` and `.coveragerc`'s `omit` list define which files are considered "production data/config" vs. "covered by tests" respectively — update both if you add a new top-level stateful file that shouldn't be unit-tested or should be included in backups.

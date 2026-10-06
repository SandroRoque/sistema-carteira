# Repository Guidelines

## Project Structure & Module Organization

This Python 3.12 project imports brokerage documents into a multi-tenant PostgreSQL portfolio database. The schema lives in `tabelas.py` and evolves through Alembic migrations in `migrations/`. The ingestion flow starts in `carrega_notas.py`, dispatches PDF parsing through `extrai_nota_de_negociacao.py` and `extractors/`, normalizes records in `transformer.py`, and persists them through `loader.py` and `database.py`. Every portfolio query must filter by `investidor_id` (see `contas.py`); `ativos` is a shared catalog. Code serving a web request must use `database.connect(usuario_id)` (row-level security); `connect_sistema()` is only for CLI tools, auth and background jobs. A new tenant-owned table needs a GRANT and an RLS policy in its migration (see `migrations/versions/0003_*`). Portfolio calculations and reporting live in top-level modules such as `portfolio.py`, `posicoes.py`, `imposto.py`, and `relatorio.py`.

The FastAPI application is under `app/`; Jinja templates belong in `app/templates/` and browser assets in `app/static/`. Tests live in `tests/`, while format and schema notes are maintained in `docs/`. Treat `notas/`, `b3-reports/`, generated `exports/` or `reports/`, and `carteira.db` as local data rather than source code.

## Build, Test, and Development Commands

- `uv sync --dev`: create or update the virtual environment from `pyproject.toml` and `uv.lock`.
- `docker compose up -d` then `uv run alembic upgrade head`: start the local Postgres and apply migrations.
- `uv run pytest -q`: run the full test suite (needs Docker; spins up a throwaway Postgres).
- `uv run alembic revision --autogenerate -m "..."`: create a migration after editing `tabelas.py`; review it before committing.
- `uv run uvicorn app.main:app --reload`: start the web UI with automatic reload.
- `uv run python carrega_notas.py`: parse configured PDFs and load the database idempotently.
- `uv run python exporta_csvs.py`: create the diagnostic Excel export.

Copy `.env.example` to `.env` before running ingestion. Set `NOTAS_DIR` and `DATABASE_URL`; `CARTEIRA_USUARIO_EMAIL` / `CARTEIRA_INVESTIDOR_ID` select the tenant for CLI tools.

## Coding Style & Naming Conventions

Use four-space indentation, type hints, and small functions with a single responsibility. Follow existing Python conventions: `snake_case` for modules, functions, and variables; `PascalCase` for dataclasses; and uppercase names for constants. Keep parsing and transformation logic pure where practical, and isolate database writes in loader/database modules. There is no configured formatter or linter, so keep imports organized and follow PEP 8 manually.

## Testing Guidelines

Tests use pytest. Name files `test_<module>.py` and tests `test_<behavior>`. Add regression cases for every new PDF layout, parser edge case, or financial calculation. Prefer fixtures in `tests/conftest.py` and builders in `tests/fabricas.py`; never depend on private documents, and use only fake CPFs with valid check digits. No coverage threshold is configured, but changed behavior should be exercised directly.

## Commit & Pull Request Guidelines

Recent commits use concise, imperative Portuguese descriptions (for example, `Adiciona extracao de PDFs...` or `corrige extracao...`). Keep each commit focused and explain the affected workflow. Pull requests should summarize the change, list verification commands, note schema or configuration changes, and link related issues. Include screenshots for UI changes and sanitized sample details for extraction fixes; never commit credentials or customer financial data.

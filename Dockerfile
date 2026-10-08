# Web app image: uvicorn serving app.main, with the import worker in-process.
# Migrations run separately (fly.toml release_command: alembic upgrade head).

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
# Dependencies first, so code changes do not reinstall them.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm
RUN useradd --create-home --uid 1000 carteira
COPY --from=build --chown=carteira:carteira /app /app
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
# Login shells (fly ssh console) reset PATH in /etc/profile; keep the venv first there too.
RUN echo 'export PATH="/app/.venv/bin:$PATH"' > /etc/profile.d/venv.sh
USER carteira
EXPOSE 8080
# One uvicorn process: the import worker is a thread in it, and parsing runs
# in a child process (isolamento.py). Proxy headers come from Fly's edge,
# the only way in, so the client IP in logs is the real one.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]

FROM python:3.12-slim

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates clamav \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir "uv>=0.8,<1"
RUN groupadd --system app && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app

COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations
COPY docs ./docs
COPY scripts ./scripts

RUN uv sync --frozen --no-dev --extra production
RUN chown -R app:app /app

EXPOSE 8000

USER app

CMD ["uv", "run", "uvicorn", "document_intelligence.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

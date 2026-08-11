FROM python:3.12-slim

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN python -m pip install --no-cache-dir "uv>=0.8,<1"

COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations
COPY docs ./docs
COPY scripts ./scripts

RUN uv sync --frozen --no-dev --extra production

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "document_intelligence.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

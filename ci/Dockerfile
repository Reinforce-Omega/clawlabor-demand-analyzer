FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY src/ ./src/
COPY main.py ./

RUN uv sync --frozen

ENV PATH="/app/.venv/bin:$PATH"

CMD ["demand-analyzer"]

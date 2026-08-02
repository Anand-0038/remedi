FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.8.0 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --extra live --no-install-project

COPY src ./src
COPY web ./web
COPY examples ./examples

RUN uv sync --frozen --no-dev --extra live

EXPOSE 8790
CMD ["/app/.venv/bin/remedi", "serve", "--host", "0.0.0.0", "--port", "8790"]

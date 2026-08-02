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

RUN groupadd --system remedi \
    && useradd --system --gid remedi --home-dir /app --shell /usr/sbin/nologin remedi \
    && mkdir -p /app/.remedi \
    && chown remedi:remedi /app/.remedi

USER remedi

EXPOSE 8790
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8790/api/health', timeout=3).read()"]
CMD ["/app/.venv/bin/remedi", "serve", "--host", "0.0.0.0", "--port", "8790"]

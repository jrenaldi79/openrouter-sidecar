# Hosted OpenRouter Sidecar (Streamable HTTP). See docs/HOSTED_OPERATIONS.md.
FROM ghcr.io/astral-sh/uv:0.6.14 AS uv

FROM python:3.12-slim AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first so code changes don't invalidate the dependency layer.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
RUN useradd --system --uid 10001 --home-dir /app sidecar
WORKDIR /app
COPY --from=build --chown=sidecar:sidecar /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8000
USER sidecar
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health',timeout=4)"
CMD ["openrouter-image-mcp", "serve-http"]

# Hosted OpenRouter Sidecar (Streamable HTTP). See docs/HOSTED_OPERATIONS.md.
# Images are pinned to their multi-arch index digest; change a tag and its digest together.
FROM ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff AS uv

FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first so code changes don't invalidate the dependency layer.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
RUN useradd --system --uid 10001 --home-dir /app sidecar
WORKDIR /app
COPY --from=build --chown=sidecar:sidecar /app /app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8000
# No USER: the entrypoint starts as root only to own the mounted asset volume,
# then drops to the sidecar user (uid 10001) before the server starts.
ENTRYPOINT ["docker-entrypoint.sh"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health',timeout=4)"
CMD ["openrouter-image-mcp", "serve-http"]

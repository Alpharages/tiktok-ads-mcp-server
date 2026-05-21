# syntax=docker/dockerfile:1
FROM python:3.12-slim

# PYTHONUNBUFFERED is required: MCP stdio transport needs JSON-RPC frames
# flushed to stdout immediately, not buffered.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Non-root user. OAuth tokens are written to ~/.tiktok_ads_mcp, i.e.
# /home/app/.tiktok_ads_mcp — mount a volume there to persist auth.
RUN useradd --create-home --uid 10001 app

WORKDIR /app

# Install dependencies first (better layer caching). pyproject reads README.md.
COPY pyproject.toml requirements.txt README.md ./
COPY src/ ./src/
RUN pip install .

COPY run_server.py ./

# Pre-create the token dir owned by the non-root user. A Docker *named* volume
# mounted here inherits this ownership (uid 10001), so token writes work on any
# fresh server with no manual chown. (A host bind-mount would NOT inherit it —
# Docker creates a missing bind source as root.)
RUN mkdir -p /home/app/.tiktok_ads_mcp \
    && chown -R app:app /app /home/app/.tiktok_ads_mcp

USER app

# Persist OAuth tokens across container runs / restarts.
VOLUME ["/home/app/.tiktok_ads_mcp"]

# Default = remote Streamable HTTP transport (for hosting). Listens on $PORT
# (default 8000) and requires MCP_AUTH_TOKEN. Run detached behind a TLS proxy.
EXPOSE 8000
CMD ["python", "-m", "tiktok_ads_mcp.http_server"]

# For local stdio use instead, override the command:
#   docker run -i --rm --env-file .env -v ...:/home/app/.tiktok_ads_mcp \
#     tiktok-ads-mcp:latest python run_server.py

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
RUN chown -R app:app /app

USER app

# Persist OAuth tokens across container runs (stdio clients launch a fresh
# container per session, so this volume is what keeps you authenticated).
VOLUME ["/home/app/.tiktok_ads_mcp"]

# This is an MCP stdio server: it must be launched with `docker run -i`
# (interactive stdin). Do NOT run it detached (-d) — there is no network port.
ENTRYPOINT ["python", "run_server.py"]

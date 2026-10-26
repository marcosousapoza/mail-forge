# syntax=docker/dockerfile:1.7
FROM python:3.15-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.18 /uv /usr/local/bin/uv
WORKDIR /build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.15-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="MailForge MCP" \
      org.opencontainers.image.description="Provider-independent IMAP/SMTP MCP server" \
      org.opencontainers.image.source="https://github.com/marcosousapoza/mail-forge" \
      org.opencontainers.image.licenses="MIT"

ENV PATH="/opt/venv/bin:$PATH" \
    HOME=/tmp \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MAILFORGE_CACHE_PATH=/var/cache/mailforge/cache.sqlite3

RUN groupadd --gid 65532 mailforge \
    && useradd --uid 65532 --gid mailforge --no-create-home --home-dir /tmp mailforge \
    && mkdir -p /app /opt/venv /var/cache/mailforge \
    && chown -R mailforge:mailforge /app /opt/venv /var/cache/mailforge \
    && chmod 0700 /var/cache/mailforge

WORKDIR /app
COPY --from=builder --chown=mailforge:mailforge /opt/venv /opt/venv

VOLUME ["/var/cache/mailforge"]
USER 65532:65532
ENTRYPOINT ["mailforge-mcp"]

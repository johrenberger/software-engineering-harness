FROM python:3.13-slim

LABEL maintainer="johrenberger"
LABEL description="software-engineering-harness runtime (slice 13)"

# Non-root user
RUN groupadd -r harness && useradd -r -g harness -d /home/harness -m harness

WORKDIR /app

# System deps for git, gh, and healthcheck
RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl \
 && rm -rf /var/lib/apt/lists/*

# Python deps first (cache-friendly)
COPY pyproject.toml /app/
COPY src/ /app/src/

RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir .

# Bundle skills + docs
COPY docs/ /app/docs/
COPY examples/ /app/examples/
COPY .env.example /app/.env.example

# Switch to non-root
USER harness

# Dashboard port
EXPOSE 8765

# Healthcheck hits /healthz on the dashboard server
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8765/healthz || exit 1

# Default: run the telegram bot. Override to run the dashboard.
CMD ["harness-telegram-bot"]

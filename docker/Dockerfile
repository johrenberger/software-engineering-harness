# Pinned via manifest-list digest for multi-arch reproducibility.
# Digest was retrieved via:
#   TOKEN=$(curl -fsS "https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/python:pull" | jq -r .token)
#   curl -fsS -I -H "Accept: application/vnd.docker.distribution.manifest.list.v2+json" \
#        -H "Authorization: Bearer $TOKEN" \
#        "https://registry-1.docker.io/v2/library/python/manifests/3.13-slim" | grep -i docker-content-digest
# Update via: `docker buildx imagetools inspect python:3.13-slim --raw`
FROM python:3.13-slim@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91

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
# G10: requirements-runtime.txt is generated from uv.lock via
# `uv export --format requirements-txt --hashes --extra dev --no-emit-project`
# and committed alongside this Dockerfile so the install is fully
# reproducible + Scorecard Pinned-Dependencies gives full credit.
COPY requirements-runtime.txt /app/

RUN pip install --no-cache-dir --force-reinstall "pip==26.1.2" \
 && pip install --no-cache-dir --require-hashes -r /app/requirements-runtime.txt

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

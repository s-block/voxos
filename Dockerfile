FROM node:22-bookworm-slim AS whatsapp-bridge-build

WORKDIR /build/whatsapp-bridge

COPY whatsapp-bridge/package.json whatsapp-bridge/package-lock.json ./
RUN npm ci

COPY whatsapp-bridge/tsconfig.json whatsapp-bridge/tsconfig.build.json ./
COPY whatsapp-bridge/src/ src/
RUN npm run build \
    && npm prune --omit=dev


FROM python:3.12-slim

ARG OCI_SOURCE=""
ARG OCI_REVISION=""

LABEL org.opencontainers.image.source=$OCI_SOURCE \
    org.opencontainers.image.revision=$OCI_REVISION

# Apply available base-image security updates before installing system dependencies.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /bin/uv

WORKDIR /app

# The Python supervisor owns the bridge as a stdio child process. Copy the
# pinned Node runtime and production-only bridge artifacts into the same image;
# no package installation or remote version lookup occurs at worker startup.
COPY --from=whatsapp-bridge-build /usr/local/bin/node /usr/local/bin/node
COPY --from=whatsapp-bridge-build /build/whatsapp-bridge/package.json /app/whatsapp-bridge/package.json
COPY --from=whatsapp-bridge-build /build/whatsapp-bridge/dist/ /app/whatsapp-bridge/dist/
COPY --from=whatsapp-bridge-build /build/whatsapp-bridge/node_modules/ /app/whatsapp-bridge/node_modules/
RUN node --version \
    && test -f /app/whatsapp-bridge/dist/index.js

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .
COPY README.md .
COPY scripts/ scripts/

# Install the project
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Health check for FastAPI server
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run FastAPI with uvicorn (no database/Redis required)
CMD ["uvicorn", "voxos.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

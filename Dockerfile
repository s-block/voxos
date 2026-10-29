FROM node:22-bookworm-slim AS whatsapp-bridge-build

WORKDIR /build/whatsapp-bridge

COPY whatsapp-bridge/package.json whatsapp-bridge/package-lock.json ./
RUN npm ci

COPY whatsapp-bridge/tsconfig.json whatsapp-bridge/tsconfig.build.json ./
COPY whatsapp-bridge/src/ src/
RUN npm run build \
    && npm prune --omit=dev

FROM node:22-bookworm-slim AS redistribution-source

WORKDIR /source
COPY whatsapp-bridge/src ./whatsapp-bridge/src
COPY whatsapp-bridge/package.json whatsapp-bridge/package-lock.json whatsapp-bridge/tsconfig.json whatsapp-bridge/tsconfig.build.json ./whatsapp-bridge/
COPY LICENSE Dockerfile THIRD_PARTY_NOTICES.md ./
COPY licenses ./licenses
RUN mkdir /sources \
    && tar -czf /sources/voxos-whatsapp-source.tar.gz .
ADD --checksum=sha256:345fa4d8ba33c98f4fca82dd7691b3f9d00af2a944f9cfb53917721998b1d9d8 https://codeload.github.com/WhiskeySockets/Baileys/tar.gz/8053b086ecc97ec3f78299561de11959bab05d39 /sources/baileys-7.0.0-rc13.tar.gz
ADD --checksum=sha256:e8237ac721bdf8c622d2f13a4035b69acccb9f0c5999aa5e88944c4f171dd174 https://codeload.github.com/WhiskeySockets/libsignal-node/tar.gz/bcea72df9ec34d9d9140ab30619cf479c7c144c7 /sources/libsignal-6.0.0.tar.gz
ADD --checksum=sha256:5baaead3b0bb20ffdb9e9ff09aa9fda08620923df77b63b436654cb5e0b3bf94 https://github.com/libvips/libvips/releases/download/v8.18.7/vips-8.18.7.tar.xz /sources/libvips-8.18.7.tar.xz
ADD --checksum=sha256:a8e96acb479c9bc1a5b7950f8f25aa6c8e1ef3a19755b45cf2f00d506aeaccc0 https://codeload.github.com/lovell/sharp-libvips/tar.gz/v1.3.4 /sources/sharp-libvips-1.3.4.tar.gz
RUN chmod 0644 /sources/*


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
COPY --from=redistribution-source /sources/ /usr/share/voxos/source/
COPY licenses/GPL-3.0.txt /usr/share/voxos/licenses/GPL-3.0.txt
COPY LICENSE THIRD_PARTY_NOTICES.md /usr/share/voxos/
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

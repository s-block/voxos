ARG NODE_IMAGE=node:24-trixie-slim@sha256:8ec5d7557396cfe32d21c3f9c13072355ceab22b584578ca4bb28af31120cffe
FROM ${NODE_IMAGE} AS source
ADD --checksum=sha256:055bdf79036e05ab39dd304d42c58adae72cec9a5c8e83458f1e7d0b257978fe https://codeload.github.com/steel-dev/steel-browser/tar.gz/e5902fc50f767702894902916ecf5489de7de457 /tmp/source.tar.gz
RUN mkdir /source && tar -xzf /tmp/source.tar.gz -C /source --strip-components=1
COPY package.json package-lock.json /source/
COPY api-package.json /source/api/package.json
COPY ui-package.json /source/ui/package.json
COPY repl-package.json /source/repl/package.json
COPY patch-source.mjs /tmp/patch-source.mjs
RUN node /tmp/patch-source.mjs


FROM ${NODE_IMAGE} AS base

WORKDIR /app

ENV NODE_ENV="production" \
    PUPPETEER_CACHE_DIR=/app/.cache \
    DISPLAY=:10 \
    PATH="/usr/bin:/app/selenium/driver:${PATH}" \
    CHROME_BIN=/usr/bin/chromium \
    CHROME_PATH=/usr/bin/chromium

LABEL org.opencontainers.image.source="https://github.com/steel-dev/steel-browser"

# Install dependencies
RUN rm -f /etc/apt/apt.conf.d/docker-clean; \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache; \
    apt-get update -qq && \
    DEBIAN_FRONTEND=noninteractive apt-get -yq dist-upgrade

# Stage 1: Build UI
FROM ${NODE_IMAGE} AS ui-build

WORKDIR /app

# Copy root workspace files for UI build
COPY --from=source /source/package.json /source/package-lock.json ./
COPY --from=source /source/ui/ ./ui/

# Install UI dependencies and build with correct base path
RUN npm ci --include=dev -w ui --ignore-scripts
RUN VITE_API_URL="" VITE_WS_URL="" npm run build -w ui -- --base=/ui

# Stage 2: Build API
FROM base AS api-build

RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
    build-essential \
    pkg-config \
    python-is-python3 \
    xvfb

# Copy root workspace files for API build
COPY --from=source /source/package.json /source/package-lock.json ./

# Remove or override the prepare script to avoid husky in Docker
RUN npm pkg set scripts.prepare="echo skip husky"

COPY --from=source /source/api/ ./api/

# Install dependencies for API
RUN npm ci --include=dev --workspace=api

# Install dependencies for recorder extension separately
RUN cd api/extensions/recorder && npm ci --include=dev && cd -

# Build the API package
RUN npm run build -w api

# Build the recorder extension
RUN cd api/extensions/recorder && \
    npm run build && \
    cd -

# Prune dev dependencies
RUN npm prune --omit=dev -w api
RUN cd api/extensions/recorder && npm prune --omit=dev && cd -

# Stage 3: Production
FROM base AS production

# Install production dependencies
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -yq --no-install-recommends \
    wget \
    nginx \
    gnupg \
    fonts-ipafont-gothic \
    fonts-wqy-zenhei \
    fonts-thai-tlwg \
    fonts-noto-core \
    fonts-freefont-ttf \
    libxss1 \
    xvfb \
    curl \
    unzip \
    dbus \
    dbus-x11 \
    procps \
    x11-xserver-utils

# Install Chrome and ChromeDriver
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    wget \
    ca-certificates \
    curl \
    unzip \
    # Download and install Chromium
    && apt-get install -y chromium chromium-driver \
    # Clean up
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /var/cache/apt/*

RUN mkdir -p /files

# Copy the built API from api-build stage
COPY --from=api-build /app /app
ADD --checksum=sha256:4664ef41d4bf5ee78cd6264ff8dca480ef80628ca91df95ca554c4d66f7831c1 https://github.com/SeleniumHQ/selenium/releases/download/selenium-4.50.0/selenium-server-4.50.0.jar /app/api/selenium/server/selenium-server.jar
RUN rm -rf /usr/local/lib/node_modules/npm /opt/yarn-* && \
    rm -f /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/yarn /usr/local/bin/yarnpkg

# Copy the built UI from ui-build stage into the API container
COPY --from=ui-build /app/ui/dist /app/ui/dist

# Copy entrypoint script
COPY --chmod=755 entrypoint.sh /app/entrypoint.sh
COPY configure-proxy.mjs nginx.conf.template /app/
RUN mkdir -p /files /home/node && chown -R node:node /files /home/node
USER 1000:1000

EXPOSE 3000 9223

ENV HOST_IP=localhost \
    DBUS_SESSION_BUS_ADDRESS=autolaunch:

ENV HOST=127.0.0.1 PORT=3001 CDP_REDIRECT_PORT=3000 DISABLE_CHROME_SANDBOX=false
ENTRYPOINT ["/app/entrypoint.sh"]

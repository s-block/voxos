FROM node:22-bookworm-slim

WORKDIR /opt/codex-runner

COPY package.json package-lock.json ./
RUN npm ci --omit=dev \
    && npm cache clean --force

ENV PATH="/opt/codex-runner/node_modules/.bin:${PATH}"

ENTRYPOINT ["codex"]

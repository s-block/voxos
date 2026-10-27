#!/bin/sh
set -eu
[ "$(id -u)" != 0 ] || { echo 'Steel must run as a non-root user' >&2; exit 1; }
[ "${DISABLE_CHROME_SANDBOX:-false}" = false ] || { echo 'Steel requires the Chromium sandbox' >&2; exit 1; }
node /app/configure-proxy.mjs
nginx -c /tmp/steel-nginx.conf
exec node /app/api/build/index.js

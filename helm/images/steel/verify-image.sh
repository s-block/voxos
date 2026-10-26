#!/usr/bin/env bash
set -Eeuo pipefail
image=${1:?Pass the built Steel image}
profile_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
container="voxos-steel-verify-$$"
environment=$(mktemp)
chmod 600 "$environment"
cleanup() { docker rm -f "$container" >/dev/null 2>&1 || true; rm -f "$environment"; }
trap cleanup EXIT
printf 'STEEL_SERVICE_TOKEN=%s\nLOG_LEVEL=warn\n' "$(openssl rand -hex 32)" > "$environment"
apparmor_args=(--security-opt no-new-privileges)
if [[ -n "${STEEL_APPARMOR_PROFILE:-}" ]]; then
  apparmor_args+=(--security-opt "apparmor=$STEEL_APPARMOR_PROFILE")
fi
docker run -d --name "$container" --user 1000:1000 --cap-drop ALL \
  --security-opt "seccomp=$profile_dir/seccomp.json" \
  "${apparmor_args[@]}" --read-only --shm-size 1g \
  --tmpfs /tmp:rw,uid=1000,gid=1000,size=1g \
  --tmpfs /home/node:rw,uid=1000,gid=1000,size=512m \
  --tmpfs /files:rw,uid=1000,gid=1000,size=512m \
  --env-file "$environment" "$image" >/dev/null
docker exec -i "$container" node --input-type=module < "$profile_dir/verify-runtime.mjs"
if docker logs "$container" 2>&1 | grep -q voxos_browser_log_fixture; then
  echo 'Browser console content reached container logs' >&2
  exit 1
fi

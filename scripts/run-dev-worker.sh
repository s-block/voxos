#!/bin/sh

set -eu

restart_delay_seconds="${VOXOS_DEV_WORKER_RESTART_DELAY_SECONDS:-1}"
child_pid=""

stop_worker() {
    if [ -n "$child_pid" ]; then
        kill -TERM "$child_pid" 2>/dev/null || true
        wait "$child_pid" 2>/dev/null || true
    fi
    exit 0
}

trap stop_worker INT TERM

while true; do
    "$@" &
    child_pid=$!
    exit_code=0
    wait "$child_pid" || exit_code=$?
    child_pid=""
    printf 'Development worker exited with status %s; restarting in %ss.\n' \
        "$exit_code" "$restart_delay_seconds" >&2
    sleep "$restart_delay_seconds" &
    child_pid=$!
    wait "$child_pid" 2>/dev/null || true
    child_pid=""
done

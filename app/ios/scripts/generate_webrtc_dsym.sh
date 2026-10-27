#!/bin/sh

set -eu

# WebRTC-SDK distributes a stripped XCFramework without a companion dSYM. App
# Store Connect still expects a dSYM whose UUID matches the embedded framework.
# Generate it from the final embedded binary so it can never drift from the
# framework included in the archive.
if [ "${ACTION:-}" != "install" ] || [ "${PLATFORM_NAME:-}" != "iphoneos" ]; then
  exit 0
fi

framework_binary="${TARGET_BUILD_DIR:?}/${FRAMEWORKS_FOLDER_PATH:?}/WebRTC.framework/WebRTC"
dsym_directory="${DWARF_DSYM_FOLDER_PATH:?}/WebRTC.framework.dSYM"
dsym_binary="${dsym_directory}/Contents/Resources/DWARF/WebRTC"

if [ ! -f "${framework_binary}" ]; then
  echo "error: Embedded WebRTC binary not found at ${framework_binary}" >&2
  exit 1
fi

xcrun dsymutil "${framework_binary}" -o "${dsym_directory}"

framework_uuids="$(xcrun dwarfdump --uuid "${framework_binary}" | awk '{ print $2 }' | sort)"
dsym_uuids="$(xcrun dwarfdump --uuid "${dsym_binary}" | awk '{ print $2 }' | sort)"

if [ -z "${framework_uuids}" ] || [ "${framework_uuids}" != "${dsym_uuids}" ]; then
  echo "error: Generated WebRTC dSYM UUIDs do not match the embedded framework" >&2
  echo "error: Framework UUIDs: ${framework_uuids:-none}" >&2
  echo "error: dSYM UUIDs: ${dsym_uuids:-none}" >&2
  exit 1
fi

echo "Generated WebRTC.framework.dSYM for UUIDs: ${dsym_uuids}"

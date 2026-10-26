#!/bin/sh

set -eu

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repository_root=$(CDPATH='' cd -- "$script_dir/.." && pwd)
release_log=$(mktemp "${TMPDIR:-/tmp}/voxos-testflight.XXXXXX")

cleanup() {
    rm -f -- "$release_log"
}
trap cleanup EXIT HUP INT TERM

cd "$repository_root/app"

if [ ! -f ios/ExportOptions-TestFlight.plist ]; then
    echo "Copy ios/ExportOptions-TestFlight.plist.example to ios/ExportOptions-TestFlight.plist and set your Apple team ID before uploading." >&2
    exit 1
fi
if [ ! -f ios/Flutter/Installation.xcconfig ]; then
    echo "Copy ios/Flutter/Installation.xcconfig.example to ios/Flutter/Installation.xcconfig and configure your bundle ID and signing team." >&2
    exit 1
fi
team_id=$(/usr/libexec/PlistBuddy -c 'Print :teamID' ios/ExportOptions-TestFlight.plist)
if [ -z "$team_id" ] || [ "$team_id" = YOUR_APPLE_TEAM_ID ]; then
    echo "Set teamID in ios/ExportOptions-TestFlight.plist to your Apple signing team before uploading." >&2
    exit 1
fi

set --
if [ -f sentry.config.json ]; then
    set -- --dart-define-from-file=sentry.config.json
fi

build_status=0
flutter build ipa --no-pub --release "$@" \
    --export-options-plist=ios/ExportOptions-TestFlight.plist \
    >"$release_log" 2>&1 || build_status=$?
cat "$release_log"

# Flutter can currently return success even when xcodebuild's export step fails.
if [ "$build_status" -ne 0 ] || grep -Fq "Encountered error while creating the IPA" "$release_log"; then
    echo "TestFlight upload failed. The archive remains in app/build/ios/archive for Xcode Organizer." >&2
    exit 1
fi

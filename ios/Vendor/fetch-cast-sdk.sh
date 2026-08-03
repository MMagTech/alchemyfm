#!/usr/bin/env bash
#
# Downloads the Google Cast SDK for iOS.
#
# The framework is 29 MB, which is why it isn't committed — it would sit in
# this public repo's history permanently and every clone would pay for it.
# Run this once after cloning:
#
#     ios/Vendor/fetch-cast-sdk.sh
#
# The dynamic build, despite Google's docs steering you to the static one.
# The static library references GTMSessionFetcher without defining it, so it
# needs that vendored separately; the dynamic framework compiles both it and
# Protobuf in with hidden visibility, so it stands alone. It is also 29 MB
# rather than 141 MB. The docs' claim that the dynamic build needs Protobuf
# >= 3.13 separately does not hold for 4.8.6 — verified with nm.
set -euo pipefail

VERSION="4.8.6"
URL="https://dl.google.com/dl/chromecast/sdk/ios/GoogleCastSDK-ios-${VERSION}_dynamic.zip"

DEST="$(cd "$(dirname "$0")" && pwd)"
FRAMEWORK="${DEST}/GoogleCast.xcframework"

if [ -d "${FRAMEWORK}" ]; then
    echo "GoogleCast.xcframework is already here. Delete it first to re-fetch."
    exit 0
fi

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

echo "Fetching Google Cast SDK ${VERSION}…"
curl -fL --progress-bar -o "${WORK}/cast.zip" "${URL}"

echo "Unpacking…"
unzip -q "${WORK}/cast.zip" -d "${WORK}/unpacked"

SRC="$(find "${WORK}/unpacked" -maxdepth 3 -name 'GoogleCast.xcframework' -type d | head -1)"
if [ -z "${SRC}" ]; then
    echo "error: GoogleCast.xcframework not found in the download." >&2
    exit 1
fi

mv "${SRC}" "${FRAMEWORK}"
echo "Installed ${FRAMEWORK}"

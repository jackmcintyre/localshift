#!/usr/bin/env bash
# write_build_stamp.sh REPO_DIR DEST_DIR
#
# Record which commit a deployed copy of LocalShift came from, so the running
# integration can report it (sensor.localshift_build). Writes DEST_DIR/build.json:
#
#   {"commit": "<full sha>", "build_time": "<UTC ISO-8601>", "dirty": <bool>}
#
# REPO_DIR  the git checkout the integration was copied from
# DEST_DIR  the deployed custom_components/localshift folder
#
# "dirty" is true when custom_components/localshift has uncommitted changes in
# REPO_DIR, i.e. the deployed code is not exactly that commit. A REPO_DIR that
# is not a git checkout stamps commit "unknown".
#
# Every deploy path must run this after copying the folder (deploy.sh does);
# build.json is gitignored, so a copy that skips it reports "unknown".

set -euo pipefail

if [ "$#" -ne 2 ]; then
    echo "usage: $0 REPO_DIR DEST_DIR" >&2
    exit 2
fi

REPO_DIR="$1"
DEST_DIR="$2"

if COMMIT=$(git -C "$REPO_DIR" rev-parse HEAD 2>/dev/null); then
    if [ -n "$(git -C "$REPO_DIR" status --porcelain -- custom_components/localshift)" ]; then
        DIRTY=true
    else
        DIRTY=false
    fi
else
    COMMIT="unknown"
    DIRTY=null
fi

BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# Write then rename, so a reader never sees a half-written file.
TMP_FILE="$DEST_DIR/build.json.tmp"
printf '{"commit": "%s", "build_time": "%s", "dirty": %s}\n' \
    "$COMMIT" "$BUILD_TIME" "$DIRTY" > "$TMP_FILE"
mv -f "$TMP_FILE" "$DEST_DIR/build.json"

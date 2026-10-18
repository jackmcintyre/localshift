#!/usr/bin/env bash
# Put the pre-step backup back on the share. The restart and the re-check
# that the old state is back are done by the caller.
set -euo pipefail
REPO=/Users/jackmcintyre/localshift
RUN="$REPO/tmp/run"
HA=/Volumes/appdata/Home-Assistant-Container
B=$(cat "$RUN/last_backup")
PREV=$(cat "$RUN/live_commit")
mv "$HA/custom_components/localshift" "$HA/backups/localshift.failed.$(date +%Y%m%d_%H%M%S)"
cp -R "$B" "$HA/custom_components/localshift"
diff -rq -x __pycache__ -x '*.pyc' -x .DS_Store \
  "$RUN/live-tree/custom_components/localshift" "$HA/custom_components/localshift" \
  && echo "restored == $PREV from $B; restart next" || { echo "RESTORE MISMATCH"; exit 1; }
date +%s > "$RUN/restart_requested_at"
echo "$PREV" > "$RUN/pending_commit"

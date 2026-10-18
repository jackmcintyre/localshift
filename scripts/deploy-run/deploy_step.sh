#!/usr/bin/env bash
# One pinned deploy step. Usage: deploy_step.sh <commit> <label>
# Stops before the restart: the restart goes through the Home Assistant MCP,
# then post_step.sh judges the result. Exit non-zero = do not restart.
set -euo pipefail
COMMIT="$1"; LABEL="$2"
REPO=/Users/jackmcintyre/localshift
RUN="$REPO/tmp/run"
WT="$RUN/deploy-wt"
export HA_CONFIG=/Volumes/appdata/Home-Assistant-Container
cd "$REPO"

echo "== $LABEL @ $COMMIT  $(date +%H:%M:%S)"
uv run python scripts/deploy_check.py --preflight || { echo "PREFLIGHT REFUSED"; exit 10; }

# What is live now must be the previous pinned commit, byte for byte.
PREV=$(cat "$RUN/live_commit")
rm -rf "$RUN/live-tree" && mkdir -p "$RUN/live-tree"
git archive "$PREV" custom_components/localshift | tar -x -C "$RUN/live-tree"
if diff -rq -x __pycache__ -x '*.pyc' -x .DS_Store \
     "$RUN/live-tree/custom_components/localshift" "$HA_CONFIG/custom_components/localshift"; then
  echo "share == $PREV"
else
  echo "SHARE DOES NOT MATCH $PREV"; exit 11
fi

# deploy.sh keys the reservation on the worktree name and branch, so reserve
# from the same worktree that deploys.
git worktree remove --force "$WT" 2>/dev/null || true
git worktree add --detach "$WT" "$COMMIT" >/dev/null
( cd "$WT" && ./deploy.sh --reserve | tail -1 )
python3 scripts/ha_entity_dump.py --out-dir "$RUN" | grep JSON | awk '{print $3}' > "$RUN/before_dump"
# deploy.sh's own exit status is not trusted (it exited non-zero after a good
# copy on step 1); the byte-for-byte diff below is the check that counts.
( cd "$WT" && ./deploy.sh --no-reload > "$RUN/deploy-$COMMIT.log" 2>&1 ) || true
grep -E "complete|Backup saved|✗" "$RUN/deploy-$COMMIT.log" || true
ls -td "$HA_CONFIG"/backups/localshift.backup.* | head -1 > "$RUN/last_backup"

# The SMB client writes back after cp returns: on 14 Oct the share was still
# flushing source files two minutes after the restart. Wait until nothing under
# the component has been written for 60 s (max 20 min) before restarting.
sync
for i in $(seq 1 120); do
  newest=$(/usr/bin/find "$HA_CONFIG/custom_components/localshift" -type f -exec stat -f '%m' {} + | sort -n | tail -1)
  age=$(( $(date +%s) - newest ))
  [ "$age" -ge 60 ] && { echo "share quiet for ${age}s"; break; }
  sleep 10
done
[ "$age" -ge 60 ] || { echo "SHARE STILL BEING WRITTEN"; exit 13; }

rm -rf "$RUN/new-tree" && mkdir -p "$RUN/new-tree"
git archive "$COMMIT" custom_components/localshift | tar -x -C "$RUN/new-tree"
diff -rq -x __pycache__ -x '*.pyc' -x .DS_Store \
  "$RUN/new-tree/custom_components/localshift" "$HA_CONFIG/custom_components/localshift" \
  && echo "share == $COMMIT, ready to restart" || { echo "COPY MISMATCH"; exit 12; }
echo "$COMMIT" > "$RUN/pending_commit"
date +%s > "$RUN/restart_requested_at"

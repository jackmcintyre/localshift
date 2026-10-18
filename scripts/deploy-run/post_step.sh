#!/usr/bin/env bash
# After the restart: wait for HA, judge, then record or report failure.
# Usage: post_step.sh [extra deploy_check flags...]   Exit 0 = pass, 1 = fail.
set -uo pipefail
REPO=/Users/jackmcintyre/localshift
RUN="$REPO/tmp/run"
export HA_CONFIG=/Volumes/appdata/Home-Assistant-Container
cd "$REPO"
REQ=$(cat "$RUN/restart_requested_at")

# Wait (max 10 min) for a log that starts after the restart and has two plans.
for i in $(seq 1 60); do
  if python3 "$RUN/fetch_log.py" "$RUN/ha-live.log" 2>/dev/null; then
    first=$(head -c 19 "$RUN/ha-live.log")
    started=$(date -j -f "%Y-%m-%d %H:%M:%S" "$first" +%s 2>/dev/null || echo 0)
    n=$(/usr/bin/grep -c "Pre-charge decision" "$RUN/ha-live.log")
    if [ "$started" -ge "$REQ" ] && [ "$n" -ge 2 ]; then echo "up: restart $first, $n plans"; break; fi
  fi
  sleep 10
done

python3 scripts/ha_entity_dump.py --out-dir "$RUN" | grep JSON | awk '{print $3}' > "$RUN/after_dump"
uv run python scripts/deploy_check.py --before "$(cat "$RUN/before_dump")" --after "$(cat "$RUN/after_dump")" \
  --component-dir "$HA_CONFIG/custom_components/localshift" --expect-recompile --wait-settled 900 "$@"
rc=$?
if [ $rc -eq 0 ]; then
  cp "$RUN/pending_commit" "$RUN/live_commit"
  ( cd "$RUN/deploy-wt" && ./deploy.sh --release | tail -1 )
  echo "PASS: live = $(cat "$RUN/live_commit")"
else
  echo "FAIL rc=$rc: roll back with rollback.sh"
fi
exit $rc

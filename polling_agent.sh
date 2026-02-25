#!/bin/bash
# polling_agent.sh - Lo-fi polling agent for LocalShift
#
# This agent monitors GitHub issues and optionally Home Assistant logs,
# using Cline CLI for AI-powered elaboration and planning.
#
# Usage:
#   ./polling_agent.sh                           # Start the polling agent
#   ENABLE_HA_MONITORING=true ./polling_agent.sh # Enable HA log monitoring
#   POLL_INTERVAL=60 ./polling_agent.sh          # Custom poll interval
#
# Environment Variables:
#   REPO                 - GitHub repository (default: jackmcintyre/localshift)
#   POLL_INTERVAL        - Seconds between polls (default: 300)
#   STATE_FILE           - Path to state file (default: /tmp/polling_agent_state.json)
#   ENABLE_HA_MONITORING - Enable HA log checking (default: false)
#   HA_LOG_PATH          - Path to HA logs (default: /homeassistant/home-assistant.log)
#   DRY_RUN              - If true, don't invoke Cline, just log what would happen

set -e

# Configuration (can be overridden via environment variables)
REPO="${REPO:-jackmcintyre/localshift}"
POLL_INTERVAL="${POLL_INTERVAL:-300}"
STATE_FILE="${STATE_FILE:-/tmp/polling_agent_state.json}"
ENABLE_HA_MONITORING="${ENABLE_HA_MONITORING:-false}"
HA_LOG_PATH="${HA_LOG_PATH:-/homeassistant/home-assistant.log}"
DRY_RUN="${DRY_RUN:-false}"

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source helper functions
source "${SCRIPT_DIR}/agent_lib.sh"

# Initialize state file if it doesn't exist
init_state

# Main polling loop
main() {
    log "=========================================="
    log "LocalShift Polling Agent Started"
    log "=========================================="
    log "Repository: $REPO"
    log "Poll interval: ${POLL_INTERVAL}s"
    log "State file: $STATE_FILE"
    log "HA monitoring: $ENABLE_HA_MONITORING"
    log "Dry run: $DRY_RUN"
    log "=========================================="
    
    while true; do
        log ""
        log "=== $(date '+%Y-%m-%d %H:%M:%S') - Polling cycle started ==="
        
        # Process GitHub issues
        process_unlabeled_issues
        process_elaborating_issues
        process_ready_to_plan_issues
        
        # Optional: Check HA logs
        if [ "$ENABLE_HA_MONITORING" = "true" ]; then
            check_ha_logs
            check_ha_forecast_debug
        fi
        
        log "=== Polling cycle complete ==="
        log "Sleeping for $POLL_INTERVAL seconds... (Ctrl+C to stop)"
        sleep "$POLL_INTERVAL"
    done
}

# Trap for graceful shutdown
trap 'log "Received shutdown signal, exiting..."; exit 0' INT TERM

# Run main function
main "$@"
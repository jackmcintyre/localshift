#!/bin/bash
# deploy.sh - Deploy LocalShift to Home Assistant
#
# This script deploys the LocalShift integration to a Home Assistant instance.
# It deploys the current worktree state (no git pull/branch operations).
#
# Prerequisites:
#   1. HA config mounted at /homeassistant (or set HA_CONFIG env var)
#   2. HA_LONG_LIVED_TOKEN env var set for API reload (optional)
#   3. HA_URL env var set for API endpoint (optional, default: http://homeassistant:8123)
#
# Usage:
#   ./deploy.sh --reserve          # Reserve HA instance (REQUIRED before deploy)
#   ./deploy.sh                    # Deploy current worktree state (requires reservation)
#   ./deploy.sh --release          # Release reservation when done
#   ./deploy.sh --status           # Show reservation status
#   ./deploy.sh --force            # Force deploy (override reservation - emergency only)
#   ./deploy.sh --no-reload        # Deploy without reloading HA integration
#   ./deploy.sh --dry-run          # Preview changes without deploying
#   ./deploy.sh --watch            # Watch for changes and auto-deploy
#   ./deploy.sh --restart          # Deploy + restart HA (requires user confirmation)
#
# ⚠️  REQUIRED WORKFLOW (strict mode enabled):
#   1. Reserve HA: ./deploy.sh --reserve
#   2. Deploy:     ./deploy.sh
#   3. Check logs to verify
#   4. If issues, fix and redeploy
#   5. If successful, open PR
#   6. Release:    ./deploy.sh --release
#
# The reservation system prevents multiple agents from overwriting each other's work.
# Deploy will FAIL if you don't have an active reservation.

set -e

# Configuration (override with environment variables)
HA_CONFIG="${HA_CONFIG:-/homeassistant}"
HA_URL="${HA_URL:-http://homeassistant:8123}"
HA_TOKEN="${HA_LONG_LIVED_TOKEN:-}"
COMPONENT_NAME="localshift"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_DIR="$SCRIPT_DIR/custom_components/$COMPONENT_NAME"
DEST_DIR="$HA_CONFIG/custom_components/$COMPONENT_NAME"

# Parse arguments
NO_RELOAD=false
DRY_RUN=false
WATCH_MODE=false
RESERVE_MODE=false
RELEASE_MODE=false
FORCE_MODE=false
STATUS_MODE=false
RESTART_MODE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --no-reload)
            NO_RELOAD=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --watch)
            WATCH_MODE=true
            shift
            ;;
        --reserve)
            RESERVE_MODE=true
            shift
            ;;
        --release)
            RELEASE_MODE=true
            shift
            ;;
        --force)
            FORCE_MODE=true
            shift
            ;;
        --status)
            STATUS_MODE=true
            shift
            ;;
        --restart)
            RESTART_MODE=true
            shift
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: $0 [--no-reload] [--dry-run] [--watch] [--reserve] [--release] [--force] [--status] [--restart]"
            exit 1
            ;;
    esac
done

# Color output (disable if not a terminal)
if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    BLUE='\033[0;34m'
    CYAN='\033[0;36m'
    NC='\033[0m' # No Color
else
    RED=''
    GREEN=''
    YELLOW=''
    BLUE=''
    CYAN=''
    NC=''
fi

log_info() { echo -e "${BLUE}ℹ${NC} $1"; }
log_success() { echo -e "${GREEN}✓${NC} $1"; }
log_warning() { echo -e "${YELLOW}⚠${NC} $1"; }
log_error() { echo -e "${RED}✗${NC} $1"; }
log_deploy() { echo -e "${CYAN}🚀${NC} $1"; }
log_reserve() { echo -e "${CYAN}🔒${NC} $1"; }

# =============================================================================
# RESERVATION SYSTEM
# =============================================================================
# Prevents multiple agents from overwriting each other's deployments

RESERVE_FILE="$HA_CONFIG/custom_components/localshift.reserve"
RESERVE_TIMEOUT=1800  # 30 minutes in seconds

# Get current worktree/branch identifier
get_agent_id() {
    local branch=$(git -C "$SCRIPT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    local worktree=$(basename "$SCRIPT_DIR")
    echo "$worktree ($branch)"
}

# Check if reservation is valid (not expired)
# Note: We don't check PID because the reserve command exits after creating
# the reservation - the timestamp-based expiration is sufficient
is_reservation_valid() {
    local reserve_file="$1"
    
    if [ ! -f "$reserve_file" ]; then
        return 1
    fi
    
    # Read reservation values
    local res_agent=""
    local res_branch=""
    local res_timestamp=""
    local res_pid=""
    
    while IFS='=' read -r key value; do
        case $key in
            AGENT) res_agent="$value" ;;
            BRANCH) res_branch="$value" ;;
            TIMESTAMP) res_timestamp="$value" ;;
            PID) res_pid="$value" ;;
        esac
    done < "$reserve_file"
    
    # Check expiration (30 minutes of inactivity)
    local now=$(date +%s)
    local reserve_time=$(date -d "$res_timestamp" +%s 2>/dev/null || echo "0")
    
    if [ $((now - reserve_time)) -gt $RESERVE_TIMEOUT ]; then
        return 1
    fi
    
    return 0
}

# Read reservation info
get_reservation_info() {
    local reserve_file="$1"
    
    if [ ! -f "$reserve_file" ]; then
        return 1
    fi
    
    while IFS='=' read -r key value; do
        case $key in
            AGENT) RES_INFO_AGENT="$value" ;;
            BRANCH) RES_INFO_BRANCH="$value" ;;
            TIMESTAMP) RES_INFO_TIMESTAMP="$value" ;;
            PID) RES_INFO_PID="$value" ;;
        esac
    done < "$reserve_file"
}

# Create reservation
create_reservation() {
    local agent_id=$(get_agent_id)
    local branch=$(git -C "$SCRIPT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    local timestamp=$(date -Iseconds)
    local pid=$$
    
    # Ensure directory exists
    mkdir -p "$(dirname "$RESERVE_FILE")"
    
    cat > "$RESERVE_FILE" << EOF
AGENT=$agent_id
BRANCH=$branch
TIMESTAMP=$timestamp
PID=$pid
EOF
    
    log_success "Reservation created: $agent_id"
    log_info "Expires in 30 minutes of inactivity"
}

# Check and handle reservation before deploy
# STRICT MODE: Requires an active reservation before deploying
check_reservation() {
    if [ "$FORCE_MODE" = true ]; then
        log_warning "Force mode - overriding any existing reservation"
        rm -f "$RESERVE_FILE"
        return 0
    fi
    
    if [ -f "$RESERVE_FILE" ]; then
        if is_reservation_valid "$RESERVE_FILE"; then
            get_reservation_info "$RESERVE_FILE"
            local current_agent=$(get_agent_id)
            
            # Allow if we're the one who reserved
            if [ "$RES_INFO_AGENT" = "$current_agent" ]; then
                log_info "You have an active reservation - proceeding"
                return 0
            fi
            
            log_error "HA instance is reserved by: $RES_INFO_AGENT"
            log_error "Branch: $RES_INFO_BRANCH"
            log_error "Since: $RES_INFO_TIMESTAMP"
            echo ""
            log_info "Options:"
            log_info "  - Wait for them to release: ./deploy.sh --release"
            log_info "  - Force override: ./deploy.sh --force"
            exit 1
        else
            log_warning "Found expired/stale reservation - removing"
            rm -f "$RESERVE_FILE"
        fi
    fi
    
    # STRICT MODE: No valid reservation found - require one
    log_error "DEPLOYMENT BLOCKED: No active reservation found"
    echo ""
    log_info "The HA instance must be reserved before deploying."
    log_info "This prevents multiple agents from overwriting each other's work."
    echo ""
    log_info "Required workflow:"
    log_info "  1. Reserve: ./deploy.sh --reserve"
    log_info "  2. Deploy:  ./deploy.sh"
    log_info "  3. Release: ./deploy.sh --release (when done)"
    echo ""
    log_info "To override (emergency): ./deploy.sh --force"
    exit 1
}

# =============================================================================
# END RESERVATION SYSTEM
# =============================================================================

# Check prerequisites
log_info "Checking prerequisites..."

if [ ! -d "$SOURCE_DIR" ]; then
    log_error "Source directory not found: $SOURCE_DIR"
    exit 1
fi

if [ ! -d "$HA_CONFIG" ]; then
    log_error "HA config directory not found: $HA_CONFIG"
    log_info "Make sure the HA config is mounted at $HA_CONFIG"
    log_info "Or set HA_CONFIG environment variable"
    exit 1
fi

# Get current branch for display
CURRENT_BRANCH=$(git -C "$SCRIPT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")

# Watch mode - monitor for changes and auto-deploy
if [ "$WATCH_MODE" = true ]; then
    log_info "Starting watch mode - monitoring for changes..."
    log_info "Current branch: $CURRENT_BRANCH"
    log_info "Press Ctrl+C to stop"
    echo ""
    
    # Check for inotifywait
    if ! command -v inotifywait &> /dev/null; then
        log_error "inotifywait not found. Install with: apt-get install inotify-tools"
        exit 1
    fi
    
    # Setup trap to release reservation on exit (Ctrl+C or unexpected termination)
    cleanup_on_exit() {
        log_info "Releasing reservation on exit..."
        "$SCRIPT_DIR/deploy.sh" --release --no-reload > /dev/null 2>&1 || true
    }
    trap cleanup_on_exit INT TERM EXIT
    
    # Initial deploy
    log_deploy "Performing initial deployment..."
    "$SCRIPT_DIR/deploy.sh" --no-reload
    
    # Track local commit for git polling
    LAST_COMMIT=$(git -C "$SCRIPT_DIR" rev-parse HEAD 2>/dev/null || echo "")
    COOLDOWN_FILE="/tmp/deploy-cooldown-$BASHPID"
    
    # Background git poller - creates marker file when remote changes detected
    (
        while true; do
            sleep 30
            git -C "$SCRIPT_DIR" fetch origin test --quiet 2>/dev/null || true
            REMOTE_COMMIT=$(git -C "$SCRIPT_DIR" rev-parse origin/test 2>/dev/null || echo "")
            if [ -n "$REMOTE_COMMIT" ] && [ "$REMOTE_COMMIT" != "$LAST_COMMIT" ]; then
                echo "1" > /tmp/git-poll-$BASHPID
            fi
        done
    ) &
    GIT_PID=$!
    
    # Watch for changes
    inotifywait -m -r -e modify,create,delete,move "$SOURCE_DIR" 2>/dev/null | while read -r path action file; do
        # Check for git poll marker (remote changes from PR merge)
        if [ -f /tmp/git-poll-$BASHPID ]; then
            rm -f /tmp/git-poll-$BASHPID
            git -C "$SCRIPT_DIR" fetch origin test --quiet 2>/dev/null || true
            REMOTE_COMMIT=$(git -C "$SCRIPT_DIR" rev-parse origin/test 2>/dev/null || echo "")
            if [ -n "$REMOTE_COMMIT" ] && [ "$REMOTE_COMMIT" != "$LAST_COMMIT" ]; then
                echo ""
                log_info "Git remote change detected - updating to latest..."
                # Set cooldown to ignore file changes during git reset
                echo "1" > "$COOLDOWN_FILE"
                git -C "$SCRIPT_DIR" reset --hard origin/test 2>/dev/null || true
                LAST_COMMIT=$(git -C "$SCRIPT_DIR" rev-parse HEAD 2>/dev/null || echo "")
                log_success "Updated to: $(git -C "$SCRIPT_DIR" log -1 --oneline 2>/dev/null | cut -d' ' -f1-5)"
                # Wait for file system to settle
                sleep 3
                rm -f "$COOLDOWN_FILE"
                log_success "Cooldown complete - resuming normal watch"
            fi
        fi
        
        # Skip file changes during cooldown (git reset causes DELETE/CREATE/MODIFY spam)
        if [ -f "$COOLDOWN_FILE" ]; then
            continue
        fi
        
        # Ignore __pycache__ and .pyc files
        if [[ "$path" == *"__pycache__"* ]] || [[ "$file" == *.pyc ]]; then
            continue
        fi
        
        echo ""
        log_info "Change detected: $path$file ($action)"
        log_deploy "Redeploying..."
        
        # Redeploy
        "$SCRIPT_DIR/deploy.sh" --no-reload
        
        # Reload integration via API
        if [ -n "$HA_TOKEN" ]; then
            log_info "Reloading integration via API..."
            ENTRY_ID=$(curl -s -X GET \
                -H "Authorization: Bearer $HA_TOKEN" \
                -H "Content-Type: application/json" \
                "${HA_URL%/}/api/config/config_entries/entry" 2>/dev/null | \
                jq -r '.[] | select(.domain == "localshift") | .entry_id' 2>/dev/null | head -1 || true)
            
            if [ -n "$ENTRY_ID" ]; then
                curl -s -X POST \
                    -H "Authorization: Bearer $HA_TOKEN" \
                    -H "Content-Type: application/json" \
                    "$HA_URL/api/services/homeassistant/reload_config_entry" \
                    -d "{\"entry_id\": \"$ENTRY_ID\"}" > /dev/null 2>&1 || true
                log_success "Integration reloaded"
            fi
        fi
        
        # NOTE: In watch mode, we keep the reservation until Ctrl+C
        # Only release on exit (handled by cleanup_on_exit trap)
        
        # Debounce: wait before processing next change to prevent rapid redeploys
        sleep 2
        
        log_success "Watch mode active - waiting for changes..."
    done
    
    kill $GIT_PID 2>/dev/null || true
    
    exit 0
fi

# Dry run mode
if [ "$DRY_RUN" = true ]; then
    log_warning "DRY RUN MODE - No changes will be made"
    echo ""
    echo "Would perform the following:"
    echo "  1. Backup: $DEST_DIR -> $HA_CONFIG/backups/${COMPONENT_NAME}.backup.<timestamp>"
    echo "  2. Copy: $SOURCE_DIR -> $DEST_DIR (current worktree state)"
    echo "  3. Cleanup backups older than 7 days"
    if [ "$NO_RELOAD" = false ] && [ -n "$HA_TOKEN" ]; then
        echo "  4. Reload integration via API: $HA_URL"
    fi
    if [ "$RESTART_MODE" = true ]; then
        echo "  5. Restart Home Assistant (requires user confirmation): $HA_URL"
    fi
    echo ""
    echo "Current branch: $CURRENT_BRANCH"
    exit 0
fi

# =============================================================================
# RESERVATION MODE HANDLERS
# =============================================================================

# Status mode - show current reservation
if [ "$STATUS_MODE" = true ]; then
    if [ -f "$RESERVE_FILE" ] && is_reservation_valid "$RESERVE_FILE"; then
        get_reservation_info "$RESERVE_FILE"
        log_reserve "HA Instance Reserved"
        echo "  Agent: $RES_INFO_AGENT"
        echo "  Branch: $RES_INFO_BRANCH"
        echo "  Since: $RES_INFO_TIMESTAMP"
        echo ""
        log_info "To release: ./deploy.sh --release"
    else
        log_info "HA Instance: NOT RESERVED"
        echo ""
        log_info "To reserve: ./deploy.sh --reserve"
    fi
    exit 0
fi

# Release mode
if [ "$RELEASE_MODE" = true ]; then
    if [ -f "$RESERVE_FILE" ]; then
        get_reservation_info "$RESERVE_FILE"
        current_agent=$(get_agent_id)
        
        if [ "$RES_INFO_AGENT" = "$current_agent" ]; then
            rm -f "$RESERVE_FILE"
            log_success "Reservation released"
        elif [ "$FORCE_MODE" = true ]; then
            rm -f "$RESERVE_FILE"
            log_warning "Force-released reservation from: $RES_INFO_AGENT"
        else
            log_error "Cannot release - reserved by: $RES_INFO_AGENT"
            log_info "Use --force to override"
            exit 1
        fi
    else
        log_info "No reservation to release"
    fi
    exit 0
fi

# Reserve mode
if [ "$RESERVE_MODE" = true ]; then
    if [ -f "$RESERVE_FILE" ] && is_reservation_valid "$RESERVE_FILE"; then
        get_reservation_info "$RESERVE_FILE"
        current_agent=$(get_agent_id)
        
        if [ "$RES_INFO_AGENT" = "$current_agent" ]; then
            log_info "You already have an active reservation"
            log_info "Since: $RES_INFO_TIMESTAMP"
        else
            log_error "Already reserved by: $RES_INFO_AGENT"
            log_info "Use --force to override"
            exit 1
        fi
    else
        # Remove stale reservation if exists
        rm -f "$RESERVE_FILE"
        create_reservation
    fi
    exit 0
fi

# Normal deploy - check reservation first
check_reservation

# Restart mode - validate prerequisites
if [ "$RESTART_MODE" = true ]; then
    if [ -z "$HA_TOKEN" ]; then
        log_error "HA_LONG_LIVED_TOKEN is required for --restart"
        log_info "Set the environment variable to enable API access"
        exit 1
    fi
fi

# =============================================================================
# END RESERVATION MODE HANDLERS
# =============================================================================

# Create backup of existing installation
if [ -d "$DEST_DIR" ]; then
    BACKUP_BASE="$HA_CONFIG/backups"
    mkdir -p "$BACKUP_BASE"
    BACKUP_DIR="$BACKUP_BASE/${COMPONENT_NAME}.backup.$(date +%Y%m%d_%H%M%S)"
    log_info "Backing up existing installation to: $BACKUP_DIR"
    mv "$DEST_DIR" "$BACKUP_DIR"
    
    # Cleanup backups older than 7 days
    log_info "Cleaning up backups older than 7 days..."
    OLD_BACKUPS=$(find "$BACKUP_BASE" -name "${COMPONENT_NAME}.backup.*" -type d -mtime +7 2>/dev/null || true)
    if [ -n "$OLD_BACKUPS" ]; then
        echo "$OLD_BACKUPS" | while read -r old_backup; do
            log_info "Removing old backup: $old_backup"
            rm -rf "$old_backup"
        done
    else
        log_info "No old backups to remove"
    fi
fi

# Copy current worktree state to HA
log_info "Copying files from worktree to HA config..."
mkdir -p "$HA_CONFIG/custom_components"
cp -r "$SOURCE_DIR" "$HA_CONFIG/custom_components/"
log_success "Files copied successfully"

# Set appropriate permissions
log_info "Setting permissions..."
chmod -R 755 "$DEST_DIR" 2>/dev/null || log_warning "Could not set permissions"

# Reload integration via HA API (skip if restart mode - full restart handles it)
if [ "$RESTART_MODE" = true ]; then
    log_info "Skipping API reload (--restart flag - full restart will be triggered after deploy)"
elif [ "$NO_RELOAD" = true ]; then
    log_info "Skipping reload (--no-reload flag)"
elif [ -z "$HA_TOKEN" ]; then
    log_warning "No HA_LONG_LIVED_TOKEN set - skipping API reload"
    log_info "You may need to restart Home Assistant or reload the integration manually"
else
    log_info "Attempting to reload integration via API..."
    
    # Get the config entry ID for localshift using jq for reliable JSON parsing
    if command -v jq &> /dev/null; then
        # Fetch the API response and validate it's valid JSON before parsing
        API_RESPONSE=$(curl -s -X GET \
            -H "Authorization: Bearer $HA_TOKEN" \
            -H "Content-Type: application/json" \
            "${HA_URL%/}/api/config/config_entries/entry" 2>/dev/null || true)
        
        # Check if we got a valid JSON response (starts with [ or {)
        if echo "$API_RESPONSE" | grep -q '^\[' 2>/dev/null; then
            ENTRY_ID=$(echo "$API_RESPONSE" | \
                jq -r '.[] | select(.domain == "localshift") | .entry_id' 2>/dev/null | head -1 || true)
        elif echo "$API_RESPONSE" | grep -q '^\{' 2>/dev/null; then
            # Response is a JSON object (might be an error or single entry)
            ENTRY_ID=$(echo "$API_RESPONSE" | \
                jq -r 'select(.domain == "localshift") | .entry_id' 2>/dev/null || true)
        else
            # Not valid JSON - could be 404, auth error, or proxy issue
            # Try to extract HTTP status code for better error message
            if echo "$API_RESPONSE" | grep -qi "404\|not found"; then
                log_warning "API endpoint not found (404) - check HA_URL is correct and API is accessible"
            elif echo "$API_RESPONSE" | grep -qi "401\|unauthorized\|forbidden"; then
                log_warning "API authentication failed - check HA_LONG_LIVED_TOKEN is valid"
            else
                log_warning "API returned non-JSON response: ${API_RESPONSE:0:100}..."
            fi
            log_info "Tip: Ensure HA_URL points to Home Assistant (e.g., http://homeassistant:8123)"
            ENTRY_ID=""
        fi
    else
        # Fallback to grep/sed if jq not available (less reliable)
        log_warning "jq not found - using fallback JSON parsing (may be less reliable)"
        API_RESPONSE=$(curl -s -X GET \
            -H "Authorization: Bearer $HA_TOKEN" \
            -H "Content-Type: application/json" \
            "${HA_URL%/}/api/config/config_entries/entry" 2>/dev/null || true)
        
        # Only try to parse if it looks like JSON
        if echo "$API_RESPONSE" | grep -q '"entry_id"' 2>/dev/null; then
            ENTRY_ID=$(echo "$API_RESPONSE" | \
                grep -o '"entry_id"[[:space:]]*:[[:space:]]*"[^"]*"' | \
                head -1 | \
                sed 's/.*"entry_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/' || true)
        else
            if echo "$API_RESPONSE" | grep -qi "404\|not found"; then
                log_warning "API endpoint not found (404) - check HA_URL is correct and API is accessible"
            elif echo "$API_RESPONSE" | grep -qi "401\|unauthorized\|forbidden"; then
                log_warning "API authentication failed - check HA_LONG_LIVED_TOKEN is valid"
            else
                log_warning "API returned non-JSON response: ${API_RESPONSE:0:100}..."
            fi
            log_info "Tip: Ensure HA_URL points to Home Assistant (e.g., http://homeassistant:8123)"
            ENTRY_ID=""
        fi
    fi
    
    if [ -n "$ENTRY_ID" ]; then
        # Reload the config entry
        RELOAD_RESULT=$(curl -s -X POST \
            -H "Authorization: Bearer $HA_TOKEN" \
            -H "Content-Type: application/json" \
            "$HA_URL/api/services/homeassistant/reload_config_entry" \
            -d "{\"entry_id\": \"$ENTRY_ID\"}" 2>/dev/null || true)
        
        if [ -z "$RELOAD_RESULT" ] || echo "$RELOAD_RESULT" | grep -q "error"; then
            log_warning "Could not reload via API - may need manual reload"
        else
            log_success "Integration reloaded via API"
        fi
    else
        log_warning "Could not find LocalShift config entry - may need manual reload"
    fi
fi

# Restart Home Assistant if requested
if [ "$RESTART_MODE" = true ]; then
    echo ""
    log_warning "HOME ASSISTANT RESTART REQUESTED"
    echo "This will restart the entire Home Assistant instance."
    echo "All automations will be unavailable for 1-5 minutes."
    echo ""
    read -p "Restart Home Assistant now? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        log_info "Initiating Home Assistant restart..."
        RESTART_RESULT=$(curl -s -X POST \
            -H "Authorization: Bearer $HA_TOKEN" \
            -H "Content-Type: application/json" \
            "$HA_URL/api/services/homeassistant/restart" \
            -d '{}' 2>/dev/null || true)
        
        if [ -z "$RESTART_RESULT" ]; then
            log_success "Home Assistant restart initiated"
        else
            log_success "Home Assistant restart initiated"
        fi
        log_info "Deployment complete - HA is restarting"
    else
        log_info "Restart declined"
        log_deploy "Deployment complete (restart skipped)"
    fi
else
    echo ""
    log_deploy "Deployment complete!"
fi

log_info "Branch: $CURRENT_BRANCH"
log_info "Version: $(grep '"version"' "$DEST_DIR/manifest.json" | sed 's/.*"version": *"\([^"]*\)".*/\1/')"

# Show backup location if one was created
if [ -n "$BACKUP_DIR" ] && [ -d "$BACKUP_DIR" ]; then
    log_info "Backup saved at: $BACKUP_DIR"
fi

# Reminder to check logs
echo ""
log_info "Next step: Check logs to verify deployment"
log_info "  tail -100 /homeassistant/home-assistant.log | grep -i localshift"
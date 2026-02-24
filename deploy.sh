#!/bin/bash
# deploy.sh - Deploy LocalShift to Home Assistant
#
# This script deploys the LocalShift integration to a Home Assistant instance.
# It requires the HA config directory to be mounted into this container.
#
# Prerequisites:
#   1. HA config mounted at /homeassistant (or set HA_CONFIG env var)
#   2. HA_LONG_LIVED_TOKEN env var set for API reload (optional)
#   3. HA_URL env var set for API endpoint (optional, default: http://homeassistant:8123)
#
# Usage:
#   ./deploy.sh                    # Deploy test branch (default)
#   ./deploy.sh --branch main      # Deploy main branch (production)
#   ./deploy.sh --branch test      # Explicitly deploy test branch
#   ./deploy.sh --no-reload        # Deploy without reloading HA integration
#   ./deploy.sh --dry-run          # Preview changes without deploying
#   ./deploy.sh --watch            # Watch for changes and auto-deploy
#
# Branch Strategy:
#   - test branch: Staging/testing, auto-deploys on push
#   - main branch: Production, manual deploy only
#   - Feature branches: Never deployed directly, merge to test first

set -e

# Configuration (override with environment variables)
HA_CONFIG="${HA_CONFIG:-/homeassistant}"
HA_URL="${HA_URL:-http://homeassistant:8123}"
HA_TOKEN="${HA_LONG_LIVED_TOKEN:-}"
COMPONENT_NAME="localshift"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_DIR="$SCRIPT_DIR/custom_components/$COMPONENT_NAME"
DEST_DIR="$HA_CONFIG/custom_components/$COMPONENT_NAME"
DEFAULT_BRANCH="test"

# Parse arguments
NO_RELOAD=false
DRY_RUN=false
WATCH_MODE=false
TARGET_BRANCH=""

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
        --branch)
            TARGET_BRANCH="$2"
            shift 2
            ;;
        --branch=*)
            TARGET_BRANCH="${1#*=}"
            shift
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: $0 [--branch test|main] [--no-reload] [--dry-run] [--watch]"
            exit 1
            ;;
    esac
done

# Default to test branch if not specified
if [ -z "$TARGET_BRANCH" ]; then
    TARGET_BRANCH="$DEFAULT_BRANCH"
fi

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

# Watch mode - monitor for changes and auto-deploy
if [ "$WATCH_MODE" = true ]; then
    log_info "Starting watch mode - monitoring for changes..."
    log_info "Target branch: $TARGET_BRANCH"
    log_info "Press Ctrl+C to stop"
    echo ""
    
    # Check for inotifywait
    if ! command -v inotifywait &> /dev/null; then
        log_error "inotifywait not found. Install with: apt-get install inotify-tools"
        exit 1
    fi
    
    # Initial deploy
    log_deploy "Performing initial deployment..."
    "$SCRIPT_DIR/deploy.sh" --branch "$TARGET_BRANCH" --no-reload
    
    # Watch for changes
    inotifywait -m -r -e modify,create,delete,move "$SOURCE_DIR" 2>/dev/null | while read -r path action file; do
        # Ignore __pycache__ and .pyc files
        if [[ "$path" == *"__pycache__"* ]] || [[ "$file" == *.pyc ]]; then
            continue
        fi
        
        echo ""
        log_info "Change detected: $path$file ($action)"
        log_deploy "Redeploying..."
        
        # Redeploy
        "$SCRIPT_DIR/deploy.sh" --branch "$TARGET_BRANCH" --no-reload
        
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
        
        log_success "Watch mode active - waiting for changes..."
    done
    
    exit 0
fi

# Dry run mode
if [ "$DRY_RUN" = true ]; then
    log_warning "DRY RUN MODE - No changes will be made"
    echo ""
    echo "Would perform the following:"
    echo "  1. Pull from branch: $TARGET_BRANCH"
    echo "  2. Backup: $DEST_DIR -> $HA_CONFIG/backups/${COMPONENT_NAME}.backup.<timestamp>"
    echo "  3. Copy: $SOURCE_DIR -> $DEST_DIR"
    echo "  4. Cleanup backups older than 7 days"
    if [ "$NO_RELOAD" = false ] && [ -n "$HA_TOKEN" ]; then
        echo "  5. Reload integration via API: $HA_URL"
    fi
    exit 0
fi

# Pull from target branch
if [ -d "$SCRIPT_DIR/.git" ] || git -C "$SCRIPT_DIR" rev-parse --git-dir > /dev/null 2>&1; then
    log_info "Pulling from branch: $TARGET_BRANCH"
    
    # Fetch the target branch
    git -C "$SCRIPT_DIR" fetch origin "$TARGET_BRANCH" 2>/dev/null || true
    
    # Check if we're on the target branch or need to checkout
    CURRENT_BRANCH=$(git -C "$SCRIPT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    
    if [ "$CURRENT_BRANCH" != "$TARGET_BRANCH" ]; then
        # We're in a worktree or different branch - pull and merge/reset
        log_info "Current branch: $CURRENT_BRANCH, pulling $TARGET_BRANCH..."
        
        # For worktrees, we just pull the remote branch content
        git -C "$SCRIPT_DIR" pull origin "$TARGET_BRANCH" --no-rebase 2>/dev/null || \
            log_warning "Could not pull from $TARGET_BRANCH - continuing with current state"
    else
        # We're on the target branch - just pull
        git -C "$SCRIPT_DIR" pull origin "$TARGET_BRANCH" 2>/dev/null || \
            log_warning "Could not pull from $TARGET_BRANCH - continuing with current state"
    fi
fi

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

# Copy new version
log_info "Copying files to HA config..."
mkdir -p "$HA_CONFIG/custom_components"
cp -r "$SOURCE_DIR" "$HA_CONFIG/custom_components/"
log_success "Files copied successfully"

# Set appropriate permissions
log_info "Setting permissions..."
chmod -R 755 "$DEST_DIR" 2>/dev/null || log_warning "Could not set permissions"

# Reload integration via HA API
if [ "$NO_RELOAD" = true ]; then
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

echo ""
log_deploy "Deployment complete!"
log_info "Branch: $TARGET_BRANCH"
log_info "Version: $(grep '"version"' "$DEST_DIR/manifest.json" | sed 's/.*"version": *"\([^"]*\)".*/\1/')"

# Show backup location if one was created
if [ -n "$BACKUP_DIR" ] && [ -d "$BACKUP_DIR" ]; then
    log_info "Backup saved at: $BACKUP_DIR"
fi
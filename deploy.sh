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
#   ./deploy.sh [--no-reload] [--dry-run]
#
# Options:
#   --no-reload   Skip the API reload step
#   --dry-run     Show what would be done without making changes

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

for arg in "$@"; do
    case $arg in
        --no-reload)
            NO_RELOAD=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        *)
            echo "Unknown argument: $arg"
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
    NC='\033[0m' # No Color
else
    RED=''
    GREEN=''
    YELLOW=''
    BLUE=''
    NC=''
fi

log_info() { echo -e "${BLUE}ℹ${NC} $1"; }
log_success() { echo -e "${GREEN}✓${NC} $1"; }
log_warning() { echo -e "${YELLOW}⚠${NC} $1"; }
log_error() { echo -e "${RED}✗${NC} $1"; }

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

# Dry run mode
if [ "$DRY_RUN" = true ]; then
    log_warning "DRY RUN MODE - No changes will be made"
    echo ""
    echo "Would perform the following:"
    echo "  1. Backup: $DEST_DIR -> ${DEST_DIR}.backup.<timestamp>"
    echo "  2. Copy: $SOURCE_DIR -> $DEST_DIR"
    if [ "$NO_RELOAD" = false ] && [ -n "$HA_TOKEN" ]; then
        echo "  3. Reload integration via API: $HA_URL"
    fi
    exit 0
fi

# Pull latest changes if in a git repo
if [ -d "$SCRIPT_DIR/.git" ] || git -C "$SCRIPT_DIR" rev-parse --git-dir > /dev/null 2>&1; then
    log_info "Pulling latest changes..."
    git -C "$SCRIPT_DIR" pull origin main || log_warning "Could not pull latest changes"
fi

# Create backup of existing installation
if [ -d "$DEST_DIR" ]; then
    BACKUP_DIR="${DEST_DIR}.backup.$(date +%Y%m%d_%H%M%S)"
    log_info "Backing up existing installation to: $BACKUP_DIR"
    mv "$DEST_DIR" "$BACKUP_DIR"
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
log_success "Deployment complete!"
log_info "Version: $(grep '"version"' "$DEST_DIR/manifest.json" | sed 's/.*"version": *"\([^"]*\)".*/\1/')"

# Show backup location if one was created
if [ -d "${DEST_DIR}.backup."* ] 2>/dev/null; then
    log_info "Backup saved at: ${DEST_DIR}.backup.*"
fi
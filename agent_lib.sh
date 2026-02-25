#!/bin/bash
# agent_lib.sh - Helper functions for the LocalShift polling agent
#
# This file contains all the helper functions used by polling_agent.sh
# for GitHub operations, Cline CLI integration, and HA monitoring.

# ==============================================================================
# LOGGING
# ==============================================================================

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2
}

warn() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARNING: $*" >&2
}

# ==============================================================================
# STATE MANAGEMENT
# ==============================================================================

init_state() {
    if [ ! -f "$STATE_FILE" ]; then
        echo '{}' > "$STATE_FILE"
        log "Initialized state file: $STATE_FILE"
    fi
}

get_state() {
    local key="$1"
    jq -r ".$key // empty" "$STATE_FILE"
}

set_state() {
    local key="$1"
    local value="$2"
    local tmp_file="${STATE_FILE}.tmp"
    
    jq --arg key "$key" --arg value "$value" \
        '.[$key] = $value' "$STATE_FILE" > "$tmp_file" && \
        mv "$tmp_file" "$STATE_FILE"
}

get_last_processed_comment() {
    local issue_number="$1"
    get_state "last_comment_${issue_number}"
}

set_last_processed_comment() {
    local issue_number="$1"
    local comment_id="$2"
    set_state "last_comment_${issue_number}" "$comment_id"
}

get_processed_errors() {
    get_state "processed_errors" | jq -r '.[] // empty'
}

add_processed_error() {
    local error_pattern="$1"
    local tmp_file="${STATE_FILE}.tmp"
    
    jq --arg pattern "$error_pattern" \
        '.processed_errors = (.processed_errors // []) + [$pattern]' \
        "$STATE_FILE" > "$tmp_file" && \
        mv "$tmp_file" "$STATE_FILE"
}

# ==============================================================================
# GITHUB OPERATIONS
# ==============================================================================

get_new_issues() {
    # Fetch issues with status: proposed label
    gh issue list --repo "$REPO" \
        --label "status: proposed" \
        --state open \
        --json number,title,body,createdAt \
        --jq '.[]' 2>/dev/null || echo ""
}

get_elaborating_issues() {
    # Fetch issues with status: elaborating label
    gh issue list --repo "$REPO" \
        --label "status: elaborating" \
        --state open \
        --json number,title,body,createdAt \
        --jq '.[]' 2>/dev/null || echo ""
}

get_ready_to_plan_issues() {
    # Fetch issues with status: ready-to-plan label
    gh issue list --repo "$REPO" \
        --label "status: ready-to-plan" \
        --state open \
        --json number,title,body,createdAt \
        --jq '.[]' 2>/dev/null || echo ""
}

get_issue_comments() {
    local issue_number="$1"
    gh issue view "$issue_number" --repo "$REPO" \
        --comments \
        --json comments \
        --jq '.comments[]' 2>/dev/null || echo ""
}

get_comments_since() {
    local issue_number="$1"
    local since_id="$2"
    
    if [ -z "$since_id" ]; then
        # Get all comments
        get_issue_comments "$issue_number"
    else
        # Get comments after the specified ID
        gh api "repos/${REPO}/issues/${issue_number}/comments" \
            --jq ".[] | select(.id > ${since_id})" 2>/dev/null || echo ""
    fi
}

post_comment() {
    local issue_number="$1"
    local body="$2"
    
    if [ "$DRY_RUN" = "true" ]; then
        log "[DRY RUN] Would post comment to issue #$issue_number:"
        log "$body"
        return 0
    fi
    
    log "Posting comment to issue #$issue_number"
    gh issue comment "$issue_number" --repo "$REPO" --body "$body"
}

update_label() {
    local issue_number="$1"
    local add_label="$2"
    local remove_label="${3:-}"
    
    if [ "$DRY_RUN" = "true" ]; then
        log "[DRY RUN] Would update labels on issue #$issue_number: +$add_label ${remove_label:+-$remove_label}"
        return 0
    fi
    
    log "Updating labels on issue #$issue_number: +$add_label ${remove_label:+-$remove_label}"
    
    if [ -n "$remove_label" ]; then
        gh issue edit "$issue_number" --repo "$REPO" \
            --add-label "$add_label" \
            --remove-label "$remove_label"
    else
        gh issue edit "$issue_number" --repo "$REPO" \
            --add-label "$add_label"
    fi
}

create_issue() {
    local title="$1"
    local body="$2"
    local labels="${3:-status: proposed}"
    
    if [ "$DRY_RUN" = "true" ]; then
        log "[DRY RUN] Would create issue: $title"
        return 0
    fi
    
    log "Creating issue: $title"
    gh issue create --repo "$REPO" \
        --title "$title" \
        --body "$body" \
        --label "$labels"
}

# ==============================================================================
# CLINE CLI INTEGRATION
# ==============================================================================

invoke_cline() {
    local task="$1"
    
    if [ "$DRY_RUN" = "true" ]; then
        log "[DRY RUN] Would invoke Cline with task:"
        log "$task"
        return 0
    fi
    
    log "Invoking Cline CLI..."
    
    # Use cline CLI with the task
    # The --non-interactive flag ensures it runs without user input
    # The --task flag passes the task description
    if command -v cline &> /dev/null; then
        cline task --yolo "$task" 2>&1 || {
            error "Cline CLI failed"
            return 1
        }
    else
        error "Cline CLI not found. Please install it first."
        return 1
    fi
}

build_elaboration_prompt() {
    local issue_number="$1"
    local title="$2"
    local body="$3"
    
    cat <<EOF
You are a polling agent for the LocalShift project.

A new GitHub issue has been created and needs elaboration:

**Issue #$issue_number: $title**

$body

Your task:
1. Analyze the issue to understand what's being requested
2. If you need more information, post a comment with clarifying questions using:
   \`gh issue comment $issue_number --repo $REPO --body "your questions"\`
3. Then update the label to 'status: elaborating' using:
   \`gh issue edit $issue_number --repo $REPO --add-label "status: elaborating" --remove-label "status: proposed"\`
4. If you have enough detail already, update the label to 'status: ready-to-plan' instead

Be concise and focused on gathering requirements. Ask specific questions that will help create a good implementation plan.
EOF
}

build_planning_prompt() {
    local issue_number="$1"
    local title="$2"
    local body="$3"
    local comments="$4"
    
    cat <<EOF
You are a polling agent for the LocalShift project.

Create a detailed implementation plan for this issue:

**Issue #$issue_number: $title**

$body

$comments

Your task:
1. Create a detailed implementation plan with:
   - Overview of the approach
   - Files that need to be modified
   - Specific changes for each file
   - Testing approach
   - Any risks or considerations
2. Post the plan as a comment using:
   \`gh issue comment $issue_number --repo $REPO --body "your plan"\`
3. Update the label to 'status: planned' using:
   \`gh issue edit $issue_number --repo $REPO --add-label "status: planned" --remove-label "status: ready-to-plan"\`

Format the plan in Markdown with clear sections.
EOF
}

# ==============================================================================
# ISSUE PROCESSING
# ==============================================================================

process_new_issues() {
    log "Checking for new issues (status: proposed)..."
    
    local count=0
    while IFS= read -r issue_json; do
        [ -z "$issue_json" ] && continue
        
        local number=$(echo "$issue_json" | jq -r '.number')
        local title=$(echo "$issue_json" | jq -r '.title')
        local body=$(echo "$issue_json" | jq -r '.body')
        
        log "Processing new issue #$number: $title"
        
        # Build prompt for Cline
        local prompt=$(build_elaboration_prompt "$number" "$title" "$body")
        
        # Invoke Cline
        if invoke_cline "$prompt"; then
            log "Successfully processed issue #$number"
        else
            error "Failed to process issue #$number"
        fi
        
        ((count++)) || true
    done < <(get_new_issues)
    
    log "Processed $count new issues"
}

process_elaborating_issues() {
    log "Checking for elaborating issues (awaiting user response)..."
    
    local count=0
    while IFS= read -r issue_json; do
        [ -z "$issue_json" ] && continue
        
        local number=$(echo "$issue_json" | jq -r '.number')
        local title=$(echo "$issue_json" | jq -r '.title')
        
        # Get last processed comment ID
        local last_comment_id=$(get_last_processed_comment "$number")
        
        # Check for new comments
        local new_comments=$(get_comments_since "$number" "$last_comment_id")
        
        if [ -n "$new_comments" ]; then
            log "Issue #$number has new comments"
            
            # Check if user indicated they're done answering
            # Look for keywords like "done", "ready", "that's all", "proceed"
            local comment_text=$(echo "$new_comments" | jq -r '.body' 2>/dev/null | tr '[:upper:]' '[:lower:]')
            
            if echo "$comment_text" | grep -qiE "done|ready|that'?s all|proceed|go ahead|start"; then
                log "User indicated readiness to proceed with issue #$number"
                update_label "$number" "status: ready-to-plan" "status: elaborating"
            else
                # Process the new comments with Cline
                local prompt="You are a polling agent for the LocalShift project.

Issue #$number has new comments from the user. Review them and:
1. If the comments answer your questions sufficiently, update the label to 'status: ready-to-plan'
2. If you need more information, post follow-up questions

New comments:
$new_comments

Use these commands:
- Post comment: \`gh issue comment $number --repo $REPO --body "your response"\`
- Update label: \`gh issue edit $number --repo $REPO --add-label "status: ready-to-plan" --remove-label "status: elaborating"\`"
                
                if invoke_cline "$prompt"; then
                    log "Successfully processed new comments on issue #$number"
                else
                    error "Failed to process comments on issue #$number"
                fi
            fi
            
            # Update last processed comment
            local latest_comment_id=$(echo "$new_comments" | jq -r 'select(.id != null) | .id' | tail -1)
            if [ -n "$latest_comment_id" ]; then
                set_last_processed_comment "$number" "$latest_comment_id"
            fi
        fi
        
        ((count++)) || true
    done < <(get_elaborating_issues)
    
    log "Checked $count elaborating issues"
}

process_ready_to_plan_issues() {
    log "Checking for issues ready to plan..."
    
    local count=0
    while IFS= read -r issue_json; do
        [ -z "$issue_json" ] && continue
        
        local number=$(echo "$issue_json" | jq -r '.number')
        local title=$(echo "$issue_json" | jq -r '.title')
        local body=$(echo "$issue_json" | jq -r '.body')
        
        log "Creating plan for issue #$number: $title"
        
        # Get all comments
        local comments=$(get_issue_comments "$number")
        local comments_text=""
        if [ -n "$comments" ]; then
            comments_text="## Comments\n\n$comments"
        fi
        
        # Build planning prompt
        local prompt=$(build_planning_prompt "$number" "$title" "$body" "$comments_text")
        
        # Invoke Cline to create plan
        if invoke_cline "$prompt"; then
            log "Successfully created plan for issue #$number"
        else
            error "Failed to create plan for issue #$number"
        fi
        
        ((count++)) || true
    done < <(get_ready_to_plan_issues)
    
    log "Created plans for $count issues"
}

# ==============================================================================
# HOME ASSISTANT LOG MONITORING
# ==============================================================================

check_ha_logs() {
    log "Checking Home Assistant logs for errors..."
    
    if [ ! -f "$HA_LOG_PATH" ]; then
        warn "HA log file not found: $HA_LOG_PATH"
        return 1
    fi
    
    # Get last 100 lines and filter for LocalShift errors
    local errors=$(tail -100 "$HA_LOG_PATH" 2>/dev/null | \
        grep -i "localshift" | \
        grep -iE "error|exception|failed" || true)
    
    if [ -z "$errors" ]; then
        log "No LocalShift errors found in HA logs"
        return 0
    fi
    
    log "Found LocalShift errors in HA logs"
    
    # Extract unique error patterns
    local patterns=$(echo "$errors" | \
        grep -oE '[A-Za-z]+\.[A-Za-z]+Error:.*|Exception:.*|failed:.*' | \
        sort -u || true)
    
    if [ -z "$patterns" ]; then
        # Fallback: use the full error lines
        patterns="$errors"
    fi
    
    # Check each pattern
    local count=0
    while IFS= read -r pattern; do
        [ -z "$pattern" ] && continue
        
        # Check if we've already processed this error
        local processed=$(get_processed_errors)
        if echo "$processed" | grep -qF "$pattern"; then
            log "Skipping already-processed error: ${pattern:0:50}..."
            continue
        fi
        
        log "Creating issue for error: ${pattern:0:50}..."
        
        # Create issue for the error
        local title="LocalShift Error: $(echo "$pattern" | cut -c1-50)"
        local body="## Error Detected in Home Assistant Logs

An error was automatically detected in the Home Assistant logs:

\`\`\`
$pattern
\`\`\`

### Context
This issue was automatically created by the polling agent.

### Next Steps
1. Investigate the error
2. Determine the root cause
3. Implement a fix"

        if create_issue "$title" "$body" "status: proposed,priority: high"; then
            # Mark as processed
            add_processed_error "$pattern"
            ((count++)) || true
        fi
    done <<< "$patterns"
    
    log "Created $count error issues"
}

# ==============================================================================
# UTILITY FUNCTIONS
# ==============================================================================

# Check if required tools are available
check_dependencies() {
    local missing=()
    
    command -v gh &> /dev/null || missing+=("gh (GitHub CLI)")
    command -v jq &> /dev/null || missing+=("jq")
    command -v cline &> /dev/null || missing+=("cline (Cline CLI)")
    
    if [ ${#missing[@]} -gt 0 ]; then
        error "Missing required dependencies:"
        for dep in "${missing[@]}"; do
            error "  - $dep"
        done
        return 1
    fi
    
    return 0
}

# Run a single polling cycle (useful for testing)
run_single_cycle() {
    log "Running single polling cycle..."
    process_new_issues
    process_elaborating_issues
    process_ready_to_plan_issues
    
    if [ "$ENABLE_HA_MONITORING" = "true" ]; then
        check_ha_logs
    fi
    
    log "Single cycle complete"
}
#!/usr/bin/env bash
#
# Check CI status for a pull request
# Usage: ./scripts/check-pr-ci.sh [PR_NUMBER]
#
# If PR_NUMBER is not provided, tries to detect from current branch

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Get PR number
PR_NUMBER="${1:-}"

if [[ -z "$PR_NUMBER" ]]; then
    # Try to get PR number from current branch
    BRANCH=$(git branch --show-current)
    PR_NUMBER=$(gh pr list --head "$BRANCH" --json number --jq '.[0].number' 2>/dev/null || echo "")
    
    if [[ -z "$PR_NUMBER" ]]; then
        echo "❌ No PR number provided and couldn't detect from current branch"
        echo "Usage: $0 [PR_NUMBER]"
        exit 1
    fi
    
    echo "📌 Detected PR #$PR_NUMBER from branch '$BRANCH'"
fi

# Check PR exists
if ! gh pr view "$PR_NUMBER" &>/dev/null; then
    echo "❌ PR #$PR_NUMBER not found"
    exit 1
fi

echo ""
echo "🔍 Checking CI status for PR #$PR_NUMBER..."
echo ""

# Get CI status
gh pr checks "$PR_NUMBER"

# Get detailed status
CHECKS_JSON=$(gh pr checks "$PR_NUMBER" --json status,conclusion,name 2>/dev/null || echo "[]")

# Count statuses
PENDING=$(echo "$CHECKS_JSON" | jq '[.[] | select(.status == "queued" or .status == "in_progress")] | length' 2>/dev/null || echo "0")
SUCCESS=$(echo "$CHECKS_JSON" | jq '[.[] | select(.status == "completed" and .conclusion == "success")] | length' 2>/dev/null || echo "0")
FAILED=$(echo "$CHECKS_JSON" | jq '[.[] | select(.status == "completed" and .conclusion == "failure")] | length' 2>/dev/null || echo "0")
TOTAL=$((PENDING + SUCCESS + FAILED))

echo ""
echo "📊 Summary:"
echo "   Total checks: $TOTAL"
echo "   ✅ Passed: $SUCCESS"
echo "   ⏳ Pending/Running: $PENDING"
echo "   ❌ Failed: $FAILED"

# Exit with appropriate code
if [[ "$FAILED" -gt 0 ]]; then
    echo ""
    echo "❌ Some checks failed. View details with: gh pr checks $PR_NUMBER --watch"
    exit 1
elif [[ "$PENDING" -gt 0 ]]; then
    echo ""
    echo "⏳ Checks still running. Monitor with: gh pr checks $PR_NUMBER --watch"
    exit 2
else
    echo ""
    echo "✅ All checks passed! PR is ready for review/merge."
    echo "   View PR: gh pr view $PR_NUMBER"
    exit 0
fi
#!/bin/bash
# Git safety verification script
# Run this to audit your git protection configuration

echo "=========================================="
echo "Git Safety Audit"
echo "=========================================="
echo ""

PASS=0
FAIL=0

check_pass() {
    echo "[PASS] $1"
    ((PASS++))
}

check_fail() {
    echo "[FAIL] $1"
    ((FAIL++))
}

# Check 1: core.hooksPath configured
HOOKS_PATH=$(git config --get core.hooksPath)
if [ "$HOOKS_PATH" = ".githooks" ]; then
    check_pass "core.hooksPath = .githooks"
else
    check_fail "core.hooksPath not set (expected: .githooks, got: ${HOOKS_PATH:-not set})"
fi

# Check 2: Hooks exist and are executable
echo ""
echo "Hook files:"
for hook in pre-commit pre-push post-checkout prepare-commit-msg; do
    if [ -x ".githooks/$hook" ]; then
        check_pass ".githooks/$hook exists and is executable"
    else
        check_fail ".githooks/$hook missing or not executable"
    fi
done

# Check 3: No worktrees on main
echo ""
echo "Worktree check:"
WORKTREES_ON_MAIN=0
while IFS= read -r line; do
    WORKTREE_PATH=$(echo "$line" | awk '{print $1}')
    WORKTREE_BRANCH=$(echo "$line" | awk '{print $NF}' | tr -d '[]')
    if [ "$WORKTREE_BRANCH" = "main" ]; then
        echo "[WARN] Worktree on main: $WORKTREE_PATH"
        WORKTREES_ON_MAIN=$((WORKTREES_ON_MAIN + 1))
    fi
done < <(git worktree list 2>/dev/null)

if [ "$WORKTREES_ON_MAIN" -eq 0 ]; then
    check_pass "No worktrees on main branch"
else
    check_fail "$WORKTREES_ON_MAIN worktree(s) on main branch"
fi

# Check 4: Current branch
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$CURRENT_BRANCH" != "main" ]; then
    check_pass "Currently on issue/feature branch ($CURRENT_BRANCH)"
else
    check_fail "Currently on main branch (use worktree for changes)"
fi

# Check 5: Main branch sync status
echo ""
echo "Main branch status:"
if git rev-parse --abbrev-ref --symbolic-full-name @{u} >/dev/null 2>&1; then
    AHEAD=$(git rev-list --count origin/main..HEAD 2>/dev/null || echo "0")
    BEHIND=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo "0")
    
    if [ "$AHEAD" -eq 0 ] && [ "$BEHIND" -eq 0 ]; then
        check_pass "main is in sync with origin/main"
    else
        if [ "$AHEAD" -gt 0 ]; then
            check_fail "main is $AHEAD commit(s) ahead of origin"
        fi
        if [ "$BEHIND" -gt 0 ]; then
            echo "[INFO] main is $BEHIND commit(s) behind origin"
        fi
    fi
else
    echo "[WARN] main has no upstream tracking"
fi

# Check 6: Recent main activity
echo ""
echo "Recent main branch activity (last 5 commits):"
git log --oneline -5 main 2>/dev/null | sed 's/^/  /' || echo "  Unable to read main log"

# Summary
echo ""
echo "=========================================="
echo "Audit Summary"
echo "=========================================="
echo "Passed: $PASS"
echo "Failed: $FAIL"
echo ""

if [ "$FAIL" -gt 0 ]; then
    echo "WARNING: $FAIL check(s) failed - review above"
    echo ""
    echo "To fix hooksPath: git config core.hooksPath .githooks"
    echo "To fix hook permissions: chmod +x .githooks/*"
    echo "To recover main: .githooks/scripts/recover-main.sh"
    exit 1
else
    echo "All checks passed - git protection is configured correctly"
    exit 0
fi

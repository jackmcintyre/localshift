---
name: github-automation-mcp
description: Automate GitHub workflows for LocalShift PRs and CI monitoring
license: MIT
compatibility: opencode
metadata:
  audience: developers
  workflow: ci-cd
---

## What I Do

Automate GitHub workflows for the LocalShift project including PR creation, CI monitoring, status checks, and branch management. I streamline the development workflow from branch creation to PR merge.

## When to Use Me

- "Create a PR for these changes"
- "Check CI status"
- "Wait for tests to pass"
- "Is the build green?"
- "Approve and merge this PR"
- "Check for merge conflicts"
- "Monitor PR #123"
- "Show me open PRs"

## Commands

### PR Creation

```bash
# Create PR from current branch
gh pr create --title "Fix battery controller timeout" \
  --body "Fixes #536" \
  --base main

# Create PR from current branch with template
gh pr create --title "Feature: Add weather correlation" \
  --body-file .github/pull_request_template.md \
  --base test
```

### CI Status Monitoring

```bash
# Check all PR checks
gh pr checks <PR_NUMBER>

# Watch CI with live updates
gh pr checks <PR_NUMBER> --watch

# Get detailed failure info
gh pr checks <PR_NUMBER> --fail-fast
```

### PR Management

```bash
# List open PRs
gh pr list --state open

# View specific PR
gh pr view <PR_NUMBER>

# Show PR diff
gh pr diff <PR_NUMBER>

# Comment on PR
gh pr comment <PR_NUMBER> --body "Coverage looks good!"
```

## Workflows

### 1. Complete PR Creation Workflow

```bash
# Step 1: Verify branch status
git status
git log --oneline -5

# Step 2: Push branch
git push -u origin $(git branch --show-current)

# Step 3: Create PR
git fetch origin
gh pr create \
  --title "$(git log -1 --pretty=%s)" \
  --body "$(cat <<'EOF'
## Summary
<!-- Brief description -->

## Changes
- 
- 

## Testing
- [ ] Unit tests pass
- [ ] Integration tests pass
- [ ] Tested in live HA

## Related Issues
Fixes #
EOF
)" \
  --base main

# Step 4: Get PR number from output and monitor
PR_NUMBER=$(gh pr view --json number --jq '.number')
echo "Created PR #$PR_NUMBER"
```

### 2. CI Monitoring Workflow

```bash
#!/bin/bash
# monitor-pr-ci.sh - Monitor CI for a PR

PR_NUMBER=$1
INTERVAL=30
MAX_WAIT=1800  # 30 minutes
ELAPSED=0

echo "Monitoring CI for PR #$PR_NUMBER..."

while [ $ELAPSED -lt $MAX_WAIT ]; do
    STATUS=$(gh pr checks $PR_NUMBER --json state --jq '.[0].state' 2>/dev/null)
    
    case $STATUS in
        "SUCCESS")
            echo "✅ All checks passed!"
            exit 0
            ;;
        "FAILURE")
            echo "❌ Checks failed!"
            gh pr checks $PR_NUMBER
            exit 1
            ;;
        "PENDING"|"")
            echo "⏳ Still running... (${ELAPSED}s elapsed)"
            sleep $INTERVAL
            ELAPSED=$((ELAPSED + INTERVAL))
            ;;
    esac
done

echo "⏰ Timeout waiting for CI"
exit 1
```

### 3. Post-PR Creation Checklist

After creating a PR:

```bash
# Get PR number
PR_NUMBER=$(gh pr view --json number --jq '.number')

# Check immediately
gh pr checks $PR_NUMBER

# If you want to wait
echo "Wait for CI results? [W]ait or [C]ontinue"
read -r choice

if [[ $choice =~ ^[Ww]$ ]]; then
    while true; do
        STATUS=$(gh pr checks $PR_NUMBER --json state --jq '.[0].state')
        if [ "$STATUS" = "SUCCESS" ]; then
            echo "✅ All checks passed!"
            break
        elif [ "$STATUS" = "FAILURE" ]; then
            echo "❌ Checks failed:"
            gh pr checks $PR_NUMBER
            break
        fi
        echo "⏳ Checking in 30s..."
        sleep 30
    done
fi
```

## Integration with Existing Workflow

The project already has CI workflows in `.github/workflows/`:

- `lint.yml` - Code quality checks
- `test.yml` - Test suite
- `duplicate-check.yml` - PR duplicate detection

### CI Check Sequence

```bash
# After PR creation, checks run automatically
gh pr checks <PR_NUMBER>

# Shows:
# ✓  lint / ruff (3.13)
# ✓  lint / vulture (3.13)
# ✓  lint / bandit (3.13)
# ✓  lint / pyright (3.13)
# ✓  lint / interrogate (3.13)
# ✓  lint / deptry (3.13)
# ✓  test / test (3.13)
# ✓  duplicate-check / check-duplicate
```

## PR Template

The project should have `.github/pull_request_template.md`:

```markdown
## Summary
<!-- Brief description of changes -->

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Refactoring
- [ ] Documentation

## Changes
<!-- List specific changes -->
- 
- 
- 

## Testing
<!-- How was this tested? -->
- [ ] Unit tests added/updated
- [ ] Integration tests pass
- [ ] Tested in live Home Assistant
- [ ] Manual testing completed

## Checklist
- [ ] Code follows project style (ruff)
- [ ] Type hints added/updated
- [ ] Documentation updated
- [ ] Tests pass locally
- [ ] No duplicate PRs

## Related Issues
Fixes #
Relates to #
```

## Automation Scripts

### Smart PR Creation

Save as `scripts/create-pr.sh`:

```bash
#!/bin/bash
set -e

# Get current branch
BRANCH=$(git branch --show-current)

# Validate not on main/mainline
if [ "$BRANCH" = "main" ] || [ "$BRANCH" = "master" ]; then
    echo "❌ Cannot create PR from main branch"
    exit 1
fi

# Check for uncommitted changes
if ! git diff-index --quiet HEAD --; then
    echo "❌ You have uncommitted changes"
    echo "Commit or stash them first"
    exit 1
fi

# Push branch
echo "📤 Pushing branch $BRANCH..."
git push -u origin $BRANCH

# Determine base branch
if [ "$BRANCH" = "test" ] || [[ "$BRANCH" == issue/auto-deploy-test* ]]; then
    BASE="test"
else
    BASE="main"
fi

# Create PR
echo "📋 Creating PR targeting $BASE..."
gh pr create \
  --title "$(git log -1 --pretty=%s)" \
  --body-file .github/pull_request_template.md \
  --base $BASE \
  --fill

# Get PR number and check CI
PR_NUMBER=$(gh pr view --json number --jq '.number')
echo "✅ Created PR #$PR_NUMBER"
echo ""
echo "Checking CI status..."
gh pr checks $PR_NUMBER
```

### CI Monitor Script

Save as `scripts/check-pr-ci.sh`:

```bash
#!/bin/bash
# Check CI status for a PR with optional wait

PR_NUMBER=${1:-$(gh pr view --json number --jq '.number')}
WAIT_MODE=${2:-"ask"}

if [ -z "$PR_NUMBER" ]; then
    echo "Usage: $0 <PR_NUMBER> [wait|nowait]"
    exit 1
fi

echo "🔍 Checking CI for PR #$PR_NUMBER..."

# Initial check
gh pr checks $PR_NUMBER

# Check status
STATUS=$(gh pr checks $PR_NUMBER --json state --jq '.[0].state' 2>/dev/null)

if [ "$STATUS" = "SUCCESS" ]; then
    echo "✅ All checks passed!"
    exit 0
elif [ "$STATUS" = "FAILURE" ]; then
    echo "❌ Some checks failed"
    exit 1
fi

# If pending, decide what to do
if [ "$WAIT_MODE" = "wait" ]; then
    echo "⏳ Waiting for CI to complete..."
    while true; do
        sleep 30
        STATUS=$(gh pr checks $PR_NUMBER --json state --jq '.[0].state')
        if [ "$STATUS" = "SUCCESS" ]; then
            echo "✅ All checks passed!"
            exit 0
        elif [ "$STATUS" = "FAILURE" ]; then
            echo "❌ Checks failed"
            gh pr checks $PR_NUMBER
            exit 1
        fi
        echo "⏳ Still running..."
    done
elif [ "$WAIT_MODE" = "ask" ]; then
    read -p "⏳ CI still running. Wait? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        exec $0 $PR_NUMBER wait
    fi
fi
```

## Tips

1. **Always check CI after PR creation** - Don't assume tests pass
2. **Use the PR template** - Ensures consistent information
3. **Monitor early** - Catch failures quickly
4. **Comment on failures** - Help reviewers understand issues
5. **Keep PRs small** - Easier to review, faster CI
6. **Target correct branch** - `main` for prod, `test` for testing
7. **Check for duplicates** - The duplicate-check workflow helps

## Commands Quick Reference

```bash
# PR lifecycle
git push -u origin $(git branch --show-current)
gh pr create --title "..." --body "..." --base main
gh pr checks <PR_NUMBER>
gh pr view <PR_NUMBER>
gh pr merge <PR_NUMBER>

# CI monitoring
gh pr checks <PR_NUMBER> --watch
gh pr checks <PR_NUMBER> --fail-fast
gh run list --workflow=test.yml

# Branch management
git fetch origin
git branch -r | grep origin
```
